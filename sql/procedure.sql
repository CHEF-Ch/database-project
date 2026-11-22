CREATE OR ALTER PROCEDURE dbo.usp_sale_statistics
    @date_from DATE, @date_to DATE, @order_count BIGINT OUTPUT,
    @sold_qty BIGINT OUTPUT, @sales_amount DECIMAL(38,2) OUTPUT,
    @refund_amount DECIMAL(38,2) OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    IF @date_from IS NULL OR @date_to IS NULL OR @date_from > @date_to
        THROW 51020, N'销售统计日期范围无效', 1;

    -- Capture order state and item snapshots once for both result sets.
    SELECT o.order_id, o.order_status, o.total_amount, o.refund_amount,
           i.product_id, i.size, i.qty, i.unit_price, p.name AS product_name,
           ROW_NUMBER() OVER (PARTITION BY o.order_id ORDER BY i.item_line) AS order_row
    INTO #sales_rows
    FROM dbo.shop_order AS o WITH (HOLDLOCK)
    LEFT JOIN dbo.order_item AS i WITH (HOLDLOCK)
      ON i.order_id = o.order_id AND o.order_status IN ('PAID', 'MAKING', 'COMPLETED')
    LEFT JOIN dbo.item_product AS p WITH (HOLDLOCK) ON p.product_id = i.product_id
    WHERE CAST(o.created_at AS DATE) BETWEEN @date_from AND @date_to
      AND o.order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED');

    SELECT @order_count = COUNT_BIG(CASE WHEN order_row = 1 AND order_status <> 'REFUNDED' THEN 1 END),
           @sold_qty = COALESCE(SUM(CAST(COALESCE(qty, 0) AS BIGINT)), 0),
           @sales_amount = COALESCE(SUM(CASE WHEN order_row = 1 AND order_status <> 'REFUNDED' THEN total_amount ELSE 0 END), 0),
           @refund_amount = COALESCE(SUM(CASE WHEN order_row = 1 AND order_status = 'REFUNDED' THEN COALESCE(refund_amount, 0) ELSE 0 END), 0)
    FROM #sales_rows;

    SELECT product_id, product_name, size, COUNT_BIG(DISTINCT order_id) AS order_count,
           SUM(CAST(qty AS BIGINT)) AS sold_qty, SUM(qty * unit_price) AS gross_amount
    FROM #sales_rows WHERE product_id IS NOT NULL
    GROUP BY product_id, product_name, size
    ORDER BY gross_amount DESC, product_id, size;
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_adjust_stock
    @material_id VARCHAR(MAX), @delta DECIMAL(10,2), @reason VARCHAR(10), @employee_id VARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    IF @@TRANCOUNT = 0
        THROW 51021, N'库存调整须在调用方事务内执行', 1;
    IF @material_id IS NULL OR DATALENGTH(@material_id) <> 8
        THROW 51022, N'原料编号长度无效', 1;
    IF @delta IS NULL OR @delta = 0 OR @reason IS NULL
       OR @reason NOT IN ('GAIN', 'SHORT', 'LOSS')
       OR (@reason = 'GAIN' AND @delta < 0)
       OR (@reason IN ('SHORT', 'LOSS') AND @delta > 0)
        THROW 51022, N'库存调整数量或原因无效', 1;
    IF @employee_id IS NULL OR DATALENGTH(@employee_id) <> 8
        THROW 51023, N'经办员工编号无效', 1;
    IF NOT EXISTS (SELECT 1 FROM dbo.staff_employee WHERE employee_id = @employee_id)
        THROW 51023, N'经办员工不存在', 1;
    IF NOT EXISTS (SELECT 1 FROM dbo.item_material WITH (UPDLOCK, HOLDLOCK) WHERE material_id = @material_id)
        THROW 51024, N'原料不存在', 1;

    UPDATE dbo.item_material
    SET stock_qty = stock_qty + @delta
    WHERE material_id = @material_id
      AND stock_qty + @delta BETWEEN reserved_qty AND 99999999.99;
    IF @@ROWCOUNT = 0
        THROW 51025, N'调整后库存超出范围或低于预占量', 1;

    INSERT dbo.inv_stock_log (material_id, log_type, qty, employee_id, created_at)
    VALUES (@material_id, @reason, ABS(@delta), @employee_id, SYSDATETIME());
END;
GO
