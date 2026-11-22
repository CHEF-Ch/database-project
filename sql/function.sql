CREATE OR ALTER FUNCTION dbo.fn_replenish_qty(@material_id CHAR(8))
RETURNS DECIMAL(10,2)
AS
BEGIN
    DECLARE @qty DECIMAL(10,2);
    SELECT @qty = CASE WHEN safety_stock > stock_qty THEN safety_stock - stock_qty ELSE 0 END
    FROM dbo.item_material WHERE material_id = @material_id;
    RETURN @qty;
END;
GO

CREATE OR ALTER FUNCTION dbo.fn_sales_summary(@date_from DATE, @date_to DATE)
RETURNS TABLE
AS RETURN
(
    SELECT p.product_id, p.name AS product_name, i.size,
           COUNT_BIG(DISTINCT o.order_id) AS order_count,
           SUM(CAST(i.qty AS BIGINT)) AS sold_qty,
           SUM(i.qty * i.unit_price) AS gross_amount
    FROM dbo.shop_order AS o
    JOIN dbo.order_item AS i ON i.order_id = o.order_id
    JOIN dbo.item_product AS p ON p.product_id = i.product_id
    WHERE CAST(o.created_at AS DATE) BETWEEN @date_from AND @date_to
      AND o.order_status IN ('PAID', 'MAKING', 'COMPLETED')
    GROUP BY p.product_id, p.name, i.size
);
GO

CREATE OR ALTER FUNCTION dbo.fn_discount(
    @subtotal DECIMAL(10,2), @threshold_amount DECIMAL(10,2), @discount_amount DECIMAL(10,2)
)
RETURNS DECIMAL(10,2)
AS
BEGIN
    IF @subtotal IS NULL OR @threshold_amount IS NULL OR @discount_amount IS NULL
       OR @subtotal < 0 OR @threshold_amount <= 0 OR @discount_amount <= 0
       OR @discount_amount > @threshold_amount
        RETURN NULL;
    RETURN CASE WHEN @subtotal >= @threshold_amount THEN @discount_amount ELSE 0 END;
END;
GO
