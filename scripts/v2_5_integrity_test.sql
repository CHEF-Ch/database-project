:ON ERROR EXIT
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

IF DB_NAME() <> 'MilkTeaShop_week11_refactor' OR OBJECT_ID('dbo.mkt_coupon_order_event', 'U') IS NULL
    THROW 51900, N'请在 v2.5 重构验收库运行。', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    INSERT dbo.item_addon_option (product_id, material_id, addon_price, addon_qty, addon_status)
    VALUES ('P0000001', 'M0000005', 1, 1, 'AVAILABLE'),
           ('P0000001', 'M0000003', 1, 1, 'AVAILABLE');
    THROW 51901, N'混合加料批次应失败。', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    IF ERROR_NUMBER() <> 51302 THROW;
END CATCH;
IF EXISTS (SELECT 1 FROM dbo.item_addon_option WHERE product_id = 'P0000001' AND material_id = 'M0000005')
    THROW 51902, N'合法行必须随非法行回滚。', 1;
PRINT N'PASS R04 合法与非法选项混合插入整体回滚';

BEGIN TRY
    BEGIN TRANSACTION;
    UPDATE dbo.item_material SET material_kind = 'INGREDIENT' WHERE material_id = 'M0000004';
    THROW 51903, N'仍有加料选项引用的原料不能改类。', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    IF ERROR_NUMBER() <> 51303 THROW;
END CATCH;
PRINT N'PASS R04 原料类别反向校验';

BEGIN TRY
    BEGIN TRANSACTION;
    INSERT dbo.order_item (order_id, item_line, product_id, size, qty, unit_price)
    VALUES ('202609160001', 99, 'P0000001', 'LARGE', 1, 12);
    THROW 51904, N'新增订单行的杯型应与商品对应。', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    IF ERROR_NUMBER() <> 51304 THROW;
END CATCH;
PRINT N'PASS R04 新订单明细杯型校验';

BEGIN TRY
    BEGIN TRANSACTION;
    INSERT dbo.order_item (order_id, item_line, product_id, size, qty, unit_price)
    VALUES ('202609160001', 99, 'P0000001', 'MEDIUM', 1, 12);
    INSERT dbo.order_item_addon (order_id, item_line, material_id, qty, addon_price)
    VALUES ('202609160001', 99, 'M0000001', 1, 2);
    THROW 51905, N'非商品选项不得成为新加料行。', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK;
    IF ERROR_NUMBER() <> 51305 THROW;
END CATCH;
PRINT N'PASS R04 新加料行必须属于对应商品';

BEGIN TRANSACTION;
UPDATE dbo.item_product SET size = 'SMALL' WHERE product_id = 'P0000001';
UPDATE dbo.order_item SET qty = qty WHERE product_id = 'P0000001';
IF NOT EXISTS (SELECT 1 FROM dbo.order_item WHERE product_id = 'P0000001' AND size = 'MEDIUM')
    THROW 51906, N'历史杯型快照应保留。', 1;
ROLLBACK;
PRINT N'PASS R04 商品改杯型后历史快照保持原值';

IF (SELECT COUNT(*) FROM dbo.mkt_coupon_order_event WHERE event_type = 'BASELINE') <> 2
    THROW 51907, N'已知基线关联应仅记一次。', 1;
PRINT N'PASS R08 重跑迁移后已知基线关联没有重复';
