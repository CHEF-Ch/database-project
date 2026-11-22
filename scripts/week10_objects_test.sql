SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;

IF NOT EXISTS (SELECT 1 FROM sys.extended_properties
               WHERE class = 0 AND name = N'milktea_schema_version'
               AND CONVERT(nvarchar(20), value) IN (N'2.0', N'2.1'))
    THROW 51100, 'Missing v2 migration marker', 1;

IF ISNULL(dbo.fn_discount(100, 100, 20), -1) <> 20
   OR ISNULL(dbo.fn_discount(99, 100, 20), -1) <> 0
   OR dbo.fn_discount(NULL, 100, 20) IS NOT NULL
   OR dbo.fn_discount(100, 10, 20) IS NOT NULL
   OR dbo.fn_discount(100, 100, -1) IS NOT NULL
    THROW 51101, 'Discount assertions failed', 1;

IF dbo.fn_replenish_qty('BAD00000') IS NOT NULL
   OR EXISTS (
       SELECT 1 FROM dbo.item_material
       WHERE dbo.fn_replenish_qty(material_id) IS NULL
          OR dbo.fn_replenish_qty(material_id)
             <> CASE WHEN safety_stock > stock_qty THEN safety_stock - stock_qty ELSE 0 END
   )
    THROW 51102, 'Replenishment assertions failed', 1;

DECLARE @orders BIGINT, @qty BIGINT, @sales DECIMAL(38,2), @refund DECIMAL(38,2);
EXEC dbo.usp_sale_statistics '00010101', '99991231', @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
IF @orders <> (SELECT COUNT(*) FROM dbo.shop_order WHERE order_status IN ('PAID','MAKING','COMPLETED'))
   OR @qty <> (SELECT COALESCE(SUM(i.qty), 0) FROM dbo.order_item AS i
               JOIN dbo.shop_order AS o ON i.order_id = o.order_id WHERE o.order_status IN ('PAID','MAKING','COMPLETED'))
   OR @sales <> (SELECT COALESCE(SUM(total_amount), 0) FROM dbo.shop_order WHERE order_status IN ('PAID','MAKING','COMPLETED'))
   OR @refund <> (SELECT COALESCE(SUM(refund_amount), 0) FROM dbo.shop_order WHERE order_status = 'REFUNDED')
    THROW 51103, 'Sales statistics assertions failed', 1;

EXEC dbo.usp_sale_statistics '99991231', '99991231', @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
IF @orders <> 0 OR @qty <> 0 OR @sales <> 0 OR @refund <> 0
    THROW 51104, 'Empty sales interval should return zero totals', 1;

BEGIN TRY
    EXEC dbo.usp_sale_statistics NULL, '99991231', @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
    THROW 51105, 'Invalid dates should be rejected', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51020 THROW;
END CATCH;

BEGIN TRY
    EXEC dbo.usp_sale_statistics '20261231', '20260101', @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
    THROW 51115, 'Reversed date range should be rejected', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51020 THROW;
END CATCH;

BEGIN TRANSACTION;
DECLARE @first DATE, @last DATE;
SELECT @first = MIN(CAST(created_at AS DATE)), @last = MAX(CAST(created_at AS DATE))
FROM dbo.shop_order WHERE order_status IN ('PAID', 'MAKING', 'COMPLETED');
IF @first IS NULL THROW 51119, 'Missing sales seed', 1;
IF @first = @last
BEGIN
    SET @last = DATEADD(DAY, 1, @first);
    UPDATE TOP (1) dbo.shop_order SET created_at = @last
    WHERE order_status IN ('PAID', 'MAKING', 'COMPLETED');
END;

EXEC dbo.usp_sale_statistics @first, @first, @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
IF @orders <> (SELECT COUNT_BIG(*) FROM dbo.shop_order
               WHERE CAST(created_at AS DATE) = @first AND order_status IN ('PAID', 'MAKING', 'COMPLETED'))
    THROW 51120, 'Start date inclusion failed', 1;

EXEC dbo.usp_sale_statistics @last, @last, @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
IF @orders <> (SELECT COUNT_BIG(*) FROM dbo.shop_order
               WHERE CAST(created_at AS DATE) = @last AND order_status IN ('PAID', 'MAKING', 'COMPLETED'))
    THROW 51121, 'End date inclusion failed', 1;

BEGIN TRANSACTION;
UPDATE dbo.item_product SET size = 'SMALL' WHERE product_id = 'P0000001';
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_sales_summary(@first, @last)
    WHERE product_id = 'P0000001' AND size = 'MEDIUM'
) OR EXISTS (
    SELECT 1 FROM dbo.fn_sales_summary(@first, @last)
    WHERE product_id = 'P0000001' AND size = 'SMALL'
)
    THROW 51122, 'Historical cup size changed with the catalog', 1;

-- Seed orders remain valid at 60 million each, but their aggregate exceeds decimal(10,2).
UPDATE dbo.shop_order SET item_subtotal = 60000000.00, coupon_discount = 0, total_amount = 60000000.00
WHERE order_status IN ('PAID', 'MAKING', 'COMPLETED');
EXEC dbo.usp_sale_statistics @first, @last, @orders OUTPUT, @qty OUTPUT, @sales OUTPUT, @refund OUTPUT;
IF @orders < 2 OR @sales <> CONVERT(DECIMAL(38,2), @orders) * 60000000
    THROW 51123, 'Wide sales aggregate failed', 1;
ROLLBACK TRANSACTION;

DECLARE @material CHAR(8), @employee CHAR(8), @stock DECIMAL(10,2), @logs BIGINT;
SELECT TOP (1) @material = material_id, @stock = stock_qty
FROM dbo.item_material WHERE stock_qty <= 99999998.99 ORDER BY material_id;
SELECT TOP (1) @employee = employee_id FROM dbo.staff_employee ORDER BY employee_id;
IF @material IS NULL OR @employee IS NULL THROW 51106, 'Missing test fixture', 1;
SELECT @logs = COUNT_BIG(*) FROM dbo.inv_stock_log;

BEGIN TRY
    BEGIN TRANSACTION;
    EXEC dbo.usp_adjust_stock @material, 1, 'GAIN', @employee;
    IF (SELECT stock_qty FROM dbo.item_material WHERE material_id = @material) <> @stock + 1
       OR (SELECT COUNT_BIG(*) FROM dbo.inv_stock_log) <> @logs + 1
        THROW 51107, 'Stock and log should update together', 1;
    EXEC dbo.usp_adjust_stock @material, 0, 'GAIN', @employee;
    THROW 51108, 'Invalid adjustment should be rejected', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    IF ERROR_NUMBER() <> 51022 THROW;
END CATCH;

IF (SELECT stock_qty FROM dbo.item_material WHERE material_id = @material) <> @stock
   OR (SELECT COUNT_BIG(*) FROM dbo.inv_stock_log) <> @logs
    THROW 51109, 'Stock and log should roll back together', 1;

-- Reject the original untruncated input, including a valid ID prefix.
DECLARE @overlong VARCHAR(20) = CONCAT(@material, '-extra');
BEGIN TRY
    BEGIN TRANSACTION;
    EXEC dbo.usp_adjust_stock @overlong, 1, 'GAIN', @employee;
    THROW 51118, 'Overlong material ID should be rejected', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    IF ERROR_NUMBER() <> 51022 THROW;
END CATCH;

DECLARE @cases TABLE (
    case_id INT PRIMARY KEY, material_id CHAR(8), delta DECIMAL(10,2),
    reason VARCHAR(10), employee_id CHAR(8), error_number INT
);
INSERT @cases VALUES
    (1, 'BAD00000', 1, 'GAIN', @employee, 51024),
    (2, @material, 1, 'GAIN', 'BAD00000', 51023),
    (3, @material, NULL, 'GAIN', @employee, 51022),
    (4, @material, 1, 'LOSS', @employee, 51022),
    (5, @material, -99999999.99, 'LOSS', @employee, 51025);
DECLARE @case INT = 1, @test_material CHAR(8), @delta DECIMAL(10,2),
        @reason VARCHAR(10), @test_employee CHAR(8), @expected_error INT;
WHILE @case <= (SELECT COUNT(*) FROM @cases)
BEGIN
    SELECT @test_material = material_id, @delta = delta, @reason = reason,
           @test_employee = employee_id, @expected_error = error_number
    FROM @cases WHERE case_id = @case;
    BEGIN TRY
        BEGIN TRANSACTION;
        EXEC dbo.usp_adjust_stock @test_material, @delta, @reason, @test_employee;
        THROW 51116, 'Invalid stock adjustment succeeded', 1;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        IF ERROR_NUMBER() <> @expected_error THROW;
    END CATCH;
    SET @case += 1;
END;

BEGIN TRANSACTION;
EXEC dbo.usp_adjust_stock @material, 1, 'GAIN', @employee;
EXEC dbo.usp_adjust_stock @material, 1, 'GAIN', @employee;
IF (SELECT stock_qty FROM dbo.item_material WHERE material_id = @material) <> @stock + 2
   OR (SELECT COUNT_BIG(*) FROM dbo.inv_stock_log) <> @logs + 2
    THROW 51117, 'Each manual adjustment must have exactly one stock change and log', 1;
ROLLBACK TRANSACTION;

PRINT 'PASS: v2 marker, three functions, two procedures, and stock rollback';
