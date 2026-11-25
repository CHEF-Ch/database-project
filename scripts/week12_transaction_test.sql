:ON ERROR EXIT
SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

IF DB_NAME() NOT LIKE 'MilkTeaShop[_]week11[_]week12%' OR OBJECT_ID('dbo.usp_complete_order', 'P') IS NULL
    THROW 51980, N'请在独立第十二周 v2.4 验收库执行。', 1;

GO
-- 固定测试编号，可复跑。清理只针对本脚本的夹具。
CREATE PROCEDURE #week12_reset
AS
BEGIN
    SET NOCOUNT ON;
    DELETE dbo.inv_stock_log WHERE order_id IN ('209912120001', '209912120002', '209912120003')
                            OR restock_id IN ('R9900001', 'R9900002', 'R9900003');
    DELETE dbo.order_material_usage WHERE order_id IN ('209912120001', '209912120002', '209912120003');
    DELETE dbo.shop_order WHERE order_id IN ('209912120001', '209912120002', '209912120003');
    DELETE dbo.inv_restock_item WHERE restock_id IN ('R9900001', 'R9900002', 'R9900003');
    DELETE dbo.inv_restock WHERE restock_id IN ('R9900001', 'R9900002', 'R9900003');
    DELETE dbo.item_material WHERE material_id IN ('M9900001', 'M9900002');
    DELETE dbo.inv_stock_audit WHERE material_id IN ('M9900001', 'M9900002');

END;
GO
EXEC #week12_reset;

INSERT dbo.item_material (material_id, name, material_kind, unit, stock_qty)
VALUES ('M9900001', N'第十二周 P', 'INGREDIENT', N'个', 10),
       ('M9900002', N'第十二周 Q', 'INGREDIENT', N'个', 8);
INSERT dbo.shop_order
    (order_id, channel, order_mode, order_status, employee_id, item_subtotal,
     coupon_discount, total_amount, pay_method, created_at, paid_at)
VALUES ('209912120001', 'COUNTER', 'COUNTER_CASHIER', 'MAKING', 'E0000001', 20, 0, 20, 'CASH', '2099-12-12', '2099-12-12'),
       ('209912120002', 'COUNTER', 'COUNTER_CASHIER', 'MAKING', 'E0000001', 20, 0, 20, 'CASH', '2099-12-12', '2099-12-12'),
       ('209912120003', 'COUNTER', 'COUNTER_CASHIER', 'MAKING', 'E0000001', 20, 0, 20, 'CASH', '2099-12-12', '2099-12-12');
INSERT dbo.order_item (order_id, item_line, product_id, size, sugar_level, ice_level, temp_level, qty, unit_price)
SELECT o.order_id, p.line, p.product_id, 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 10
FROM (VALUES ('209912120001'), ('209912120002'), ('209912120003')) AS o(order_id)
CROSS JOIN (VALUES (1, 'P0000001'), (2, 'P0000003')) AS p(line, product_id);
INSERT dbo.order_material_usage (order_id, material_id, qty)
SELECT o.order_id, m.material_id, 1
FROM (VALUES ('209912120001'), ('209912120002'), ('209912120003')) AS o(order_id)
CROSS JOIN (VALUES ('M9900001'), ('M9900002')) AS m(material_id);
INSERT dbo.inv_stock_log (material_id, log_type, qty, order_id, employee_id, created_at)
SELECT material_id, 'SALE', qty, order_id, 'E0000001', '2099-12-12'
FROM dbo.order_material_usage WHERE order_id IN ('209912120001', '209912120002');
INSERT dbo.inv_restock (restock_id, supplier_name, employee_id, created_at)
VALUES ('R9900001', N'模拟供应商', 'E0000003', '2099-12-12'),
       ('R9900002', N'模拟供应商', 'E0000003', '2099-12-12'),
       ('R9900003', N'模拟供应商', 'E0000003', '2099-12-12');
INSERT dbo.inv_restock_item (restock_id, material_id, qty)
VALUES ('R9900001', 'M9900001', 5), ('R9900002', 'M9900001', 2), ('R9900002', 'M9900002', 1);
GO

-- 完成单已在支付时扣料。独立调用后从下一批次验证真实提交。
EXECUTE AS USER = 'login_app';
SET IMPLICIT_TRANSACTIONS ON;
EXEC dbo.usp_complete_order '209912120001', '2099-12-12T01:00:00';
IF @@TRANCOUNT <> 0 OR (@@OPTIONS & 2) = 0
    THROW 51992, N'隐式事务模式下订单完成未提交或改变了会话设置。', 1;
EXEC dbo.usp_complete_order '209912120001', '2099-12-12T02:00:00';
IF @@TRANCOUNT <> 0
    THROW 51992, N'隐式事务模式下重复完成留下事务。', 1;
SET IMPLICIT_TRANSACTIONS OFF;
REVERT;
GO
IF @@TRANCOUNT <> 0 OR NOT EXISTS (
    SELECT 1 FROM dbo.shop_order WHERE order_id = '209912120001' AND order_status = 'COMPLETED'
      AND completed_at = '2099-12-12T01:00:00')
   OR (SELECT stock_qty FROM dbo.item_material WHERE material_id = 'M9900001') <> 10
   OR (SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id = '209912120001') <> 2
    THROW 51981, N'完成提交、重复调用或库存一致性失败。', 1;
PRINT N'PASS 订单提交与重复执行：保留完成时间，未重复扣料或写流水';

BEGIN TRY
    BEGIN TRANSACTION;
    EXEC dbo.usp_complete_order '209912120002', '2099-12-12T01:00:00';
    IF NOT EXISTS (SELECT 1 FROM dbo.shop_order WHERE order_id = '209912120002' AND order_status = 'COMPLETED')
        THROW 51982, N'故障前应已写入完成状态。', 1;
    THROW 51983, N'模拟调用方后续写入失败。', 1;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    IF ERROR_NUMBER() <> 51983 THROW;
END CATCH;
IF NOT EXISTS (SELECT 1 FROM dbo.shop_order WHERE order_id = '209912120002'
               AND order_status = 'MAKING' AND completed_at IS NULL)
    THROW 51984, N'订单事务未恢复。', 1;
PRINT N'PASS 订单完成已写入后主动制造故障，调用方回滚恢复制作中';

BEGIN TRY
    EXEC dbo.usp_complete_order '209912120003', '2099-12-12T01:00:00';
    THROW 51985, N'缺扣料流水的订单应拒绝。', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51117 THROW;
END CATCH;
IF @@TRANCOUNT <> 0 OR NOT EXISTS (
    SELECT 1 FROM dbo.shop_order WHERE order_id = '209912120003' AND order_status = 'MAKING'
      AND completed_at IS NULL)
    THROW 51986, N'订单拒绝后留下了状态变更或未结束事务。', 1;
PRINT N'PASS 缺支付扣料证据的订单拒绝完成并回滚';

-- 独立夹具只移除明细，确认命中 51114 而非缺 SALE 的 51117。
DELETE dbo.order_item WHERE order_id = '209912120003';
SET IMPLICIT_TRANSACTIONS ON;
BEGIN TRY
    EXEC dbo.usp_complete_order '209912120003', '2099-12-12T01:00:00';
    THROW 51993, N'无明细订单应拒绝。', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51114 THROW;
END CATCH;
IF @@TRANCOUNT <> 0 OR (@@OPTIONS & 2) = 0
    THROW 51994, N'无明细订单拒绝后事务或会话状态异常。', 1;
SET IMPLICIT_TRANSACTIONS OFF;
IF NOT EXISTS (SELECT 1 FROM dbo.shop_order WHERE order_id = '209912120003'
               AND order_status = 'MAKING' AND completed_at IS NULL)
    THROW 51995, N'无明细订单拒绝后状态异常。', 1;
PRINT N'PASS 无明细订单触发 51114 并回滚，隐式事务模式保持开启';
GO

EXECUTE AS USER = 'login_stock';
SET IMPLICIT_TRANSACTIONS ON;
EXEC dbo.usp_receive_restock 'R9900001', 'E0000003', '2099-12-12T01:00:00';
IF @@TRANCOUNT <> 0 OR (@@OPTIONS & 2) = 0
    THROW 51996, N'隐式事务模式下补货未提交或改变了会话设置。', 1;
EXEC dbo.usp_receive_restock 'R9900001', 'E0000003', '2099-12-12T02:00:00';
IF @@TRANCOUNT <> 0
    THROW 51996, N'隐式事务模式下重复验收留下事务。', 1;
SET IMPLICIT_TRANSACTIONS OFF;
REVERT;
GO
IF @@TRANCOUNT <> 0 OR NOT EXISTS (
    SELECT 1 FROM dbo.inv_restock WHERE restock_id = 'R9900001'
      AND restock_status = 'RECEIVED' AND received_at = '2099-12-12T01:00:00')
   OR (SELECT stock_qty FROM dbo.item_material WHERE material_id = 'M9900001') <> 15
   OR (SELECT COUNT(*) FROM dbo.inv_stock_log WHERE restock_id = 'R9900001') <> 1
   OR (SELECT COUNT(*) FROM dbo.inv_stock_audit WHERE material_id = 'M9900001'
       AND change_type = 'UPDATE' AND operator_id = 'E0000003' AND change_qty = 5) <> 1
    THROW 51987, N'补货提交或幂等检查失败。', 1;
PRINT N'PASS 补货提交及重复调用：库存 10→15，只落一条 IN 流水和一条验收员工审计';

-- P 按编号先写库存、流水及审计，Q 已达库存上限，第二笔更新溢出。
UPDATE dbo.item_material SET stock_qty = 99999999.99 WHERE material_id = 'M9900002';
SET IMPLICIT_TRANSACTIONS ON;
BEGIN TRY
    EXEC dbo.usp_receive_restock 'R9900002', 'E0000003', '2099-12-12T01:00:00';
    THROW 51988, N'第二笔写入应溢出。', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 8115 THROW;
END CATCH;
IF @@TRANCOUNT <> 0 OR (@@OPTIONS & 2) = 0
    THROW 51997, N'补货失败后事务或会话状态异常。', 1;
SET IMPLICIT_TRANSACTIONS OFF;
IF @@TRANCOUNT <> 0 OR SESSION_CONTEXT(N'employee_id') IS NOT NULL OR NOT EXISTS (
    SELECT 1 FROM dbo.inv_restock WHERE restock_id = 'R9900002'
      AND restock_status = 'PENDING' AND received_at IS NULL)
   OR (SELECT stock_qty FROM dbo.item_material WHERE material_id = 'M9900001') <> 15
   OR (SELECT stock_qty FROM dbo.item_material WHERE material_id = 'M9900002') <> 99999999.99
   OR EXISTS (SELECT 1 FROM dbo.inv_stock_log WHERE restock_id = 'R9900002')
   OR EXISTS (SELECT 1 FROM dbo.inv_stock_audit WHERE material_id = 'M9900001' AND change_qty = 2)
    THROW 51989, N'补货失败留下了库存、流水、审计、单据或会话状态。', 1;
PRINT N'PASS 第二笔补货溢出后，第一笔库存、流水和审计一起回滚';

BEGIN TRY
    EXEC dbo.usp_receive_restock 'R9900003', 'E0000003', '2099-12-12T01:00:00';
    THROW 51990, N'空补货单应被拒绝。', 1;
END TRY
BEGIN CATCH
    IF ERROR_NUMBER() <> 51203 THROW;
END CATCH;
IF @@TRANCOUNT <> 0 OR NOT EXISTS (
    SELECT 1 FROM dbo.inv_restock WHERE restock_id = 'R9900003' AND restock_status = 'PENDING')
    THROW 51991, N'空补货单拒绝后状态异常。', 1;
PRINT N'PASS 空补货单被拒绝，测试结束后清理独立夹具';

EXEC #week12_reset;
