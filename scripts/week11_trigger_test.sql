/* ============================================================================
   scripts/week11_trigger_test.sql —— 第十一周 ⑤ 触发器批量与异常测试
   ----------------------------------------------------------------------------
   覆盖：
     审计触发器  单条插入 / 单条修改 / 多条修改 / 删除 / 仅改预占 / 会话操作人 /
                 登录回退 / role_customer 预占权限 / 不重复写业务流水
     完成触发器  合法完成 / 重复完成 / 非法来源状态 / 缺明细 / 金额不符 /
                 无 SALE 扣料 / 完成时间缺失 / 两单同时完成 / 合法非法混合整体回滚 /
                 商品改杯型后历史订单仍可完成
   运行（验收库，勿对源库执行）：
     sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d MilkTeaShop_week11_x -i scripts/week11_trigger_test.sql

   注意：SQL Server 中触发器 THROW 会把当前事务标记为不可提交（XACT_STATE=-1），
   所以每个「预期失败」用例各自开事务，捕获后 ROLLBACK，再进入下一用例；
   不能用一个外层事务串完所有反例。
   ============================================================================ */
:ON ERROR EXIT
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET XACT_ABORT OFF;

IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb', N'MilkTeaShop', N'MilkTeaShop_v01')
    THROW 51900, N'请对第十一周验收库运行本测试，勿对源库执行。', 1;

IF OBJECT_ID('dbo.inv_stock_audit', 'U') IS NULL
   OR OBJECT_ID('dbo.trg_item_material_audit', 'TR') IS NULL
   OR OBJECT_ID('dbo.trg_shop_order_complete', 'TR') IS NULL
    THROW 51901, N'v2.3 对象未部署：请先执行 sql/migration_v2_3_audit.sql。', 1;

PRINT N'== 0. 对象存在性 ==';
PRINT N'PASS 0.1 inv_stock_audit 与两个触发器已部署';
GO

/* ---------------------------------------------------------------------------
   固定样例：每单两杯（2 × 10.00）+ 一份加料（1 × 2.00 快照）= 券前 22.00
   #w11_order 只写数据、不提交，由调用方事务控制。
   --------------------------------------------------------------------------- */
CREATE PROCEDURE #w11_order
    @order_id     CHAR(12),
    @status       VARCHAR(20),
    @subtotal     DECIMAL(10,2),
    @total        DECIMAL(10,2),
    @pay_method   VARCHAR(20)  = NULL,
    @paid_at      DATETIME2(0) = NULL,
    @completed_at DATETIME2(0) = NULL,
    @with_item    BIT = 1,
    @with_addon   BIT = 1,
    @with_usage   BIT = 1,
    @with_sale    BIT = 1,
    @item_qty     INT = 2,
    @item_price   DECIMAL(10,2) = 10.00,
    @addon_qty    INT = 1,
    @addon_price  DECIMAL(10,2) = 2.00
AS
BEGIN
    SET NOCOUNT ON;
    INSERT dbo.shop_order
        (order_id, channel, order_mode, order_status, member_id, employee_id,
         item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no,
         created_at, paid_at, completed_at, refund_amount, refund_at)
    VALUES
        (@order_id, 'COUNTER', 'COUNTER_CASHIER', @status, NULL, 'E0000001',
         @subtotal, 0, @total, @pay_method, NULL,
         '2099-01-01', @paid_at, @completed_at, NULL, NULL);

    IF @with_item = 1
        INSERT dbo.order_item
            (order_id, item_line, product_id, size, sugar_level, ice_level, temp_level, qty, unit_price)
        VALUES (@order_id, 1, 'P0000001', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', @item_qty, @item_price);

    IF @with_addon = 1
        INSERT dbo.order_item_addon (order_id, item_line, material_id, qty, addon_price)
        VALUES (@order_id, 1, 'M0000001', @addon_qty, @addon_price);

    IF @with_usage = 1
        INSERT dbo.order_material_usage (order_id, material_id, qty)
        VALUES (@order_id, 'M0000001', @addon_qty);

    IF @with_sale = 1
        INSERT dbo.inv_stock_log (material_id, log_type, qty, order_id, employee_id, created_at)
        VALUES ('M0000001', 'SALE', @addon_qty, @order_id, 'E0000001', '2099-01-01');
END;
GO

/* 上一次运行若在清理前中断，先清掉固定夹具，保证脚本可重复运行 */
DELETE dbo.inv_stock_log        WHERE order_id LIKE '20990101%';
DELETE dbo.order_material_usage WHERE order_id LIKE '20990101%';
DELETE dbo.order_item_addon     WHERE order_id LIKE '20990101%';
DELETE dbo.order_item           WHERE order_id LIKE '20990101%';
DELETE dbo.shop_order           WHERE order_id LIKE '20990101%';
DELETE dbo.inv_stock_audit      WHERE material_id = 'M0000099';
DELETE dbo.item_material        WHERE material_id = 'M0000099';
GO

/* ===========================================================================
   一、库存审计触发器
   =========================================================================== */

PRINT N'== 1. 审计：单条插入 ==';
BEGIN TRANSACTION;
    INSERT dbo.item_material (material_id, name, unit, material_kind, stock_qty, reserved_qty, safety_stock)
    VALUES ('M0000099', N'测试原料', N'克', 'INGREDIENT', 10.00, 0, 5.00);

    IF (SELECT COUNT(*) FROM dbo.inv_stock_audit WHERE material_id = 'M0000099') <> 1
        THROW 51910, N'插入原料后应恰有 1 条审计。', 1;
    IF EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000099'
          AND (before_qty <> 0 OR after_qty <> 10 OR change_qty <> 10 OR change_type <> 'INSERT')
    )
        THROW 51911, N'插入审计的前后值/变化量/类型不正确。', 1;
ROLLBACK;
PRINT N'PASS 1 插入记 0 → 10、change_qty=10、INSERT';

PRINT N'== 2. 审计：单条修改 ==';
BEGIN TRANSACTION;
    INSERT dbo.item_material (material_id, name, unit, material_kind, stock_qty, reserved_qty, safety_stock)
    VALUES ('M0000099', N'测试原料', N'克', 'INGREDIENT', 10.00, 0, 5.00);
    UPDATE dbo.item_material SET stock_qty = 15.00 WHERE material_id = 'M0000099';

    IF (SELECT COUNT(*) FROM dbo.inv_stock_audit WHERE material_id = 'M0000099' AND change_type = 'UPDATE') <> 1
        THROW 51912, N'修改现存量后应恰有 1 条 UPDATE 审计。', 1;
    IF EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000099' AND change_type = 'UPDATE'
          AND (before_qty <> 10 OR after_qty <> 15 OR change_qty <> 5)
    )
        THROW 51913, N'修改审计的 before/after/delta 不正确。', 1;
ROLLBACK;
PRINT N'PASS 2 修改记 10 → 15、change_qty=5';

PRINT N'== 3. 审计：一条语句改多行 ==';
BEGIN TRANSACTION;
    UPDATE dbo.item_material SET stock_qty = stock_qty + 1
    WHERE material_id IN ('M0000001', 'M0000002', 'M0000003');

    IF (SELECT COUNT(*) FROM dbo.inv_stock_audit WHERE material_id IN ('M0000001', 'M0000002', 'M0000003')) <> 3
        THROW 51914, N'多行 UPDATE 应写 3 条审计。', 1;
    IF EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id IN ('M0000001', 'M0000002', 'M0000003') AND change_qty <> 1
    )
        THROW 51915, N'多行审计的 change_qty 应全为 1。', 1;
ROLLBACK;
PRINT N'PASS 3 多行 UPDATE → 3 条审计、行间对应正确';

PRINT N'== 4. 审计：仅改 reserved_qty 不记审计 ==';
BEGIN TRANSACTION;
    UPDATE dbo.item_material SET reserved_qty = reserved_qty + 1 WHERE material_id = 'M0000001';
    IF EXISTS (SELECT 1 FROM dbo.inv_stock_audit WHERE material_id = 'M0000001')
        THROW 51916, N'仅改预占不应产生审计（预占不是库房实存变动）。', 1;
ROLLBACK;
PRINT N'PASS 4 仅改 reserved_qty → 0 条审计';

PRINT N'== 5. 审计：删除原料 ==';
BEGIN TRANSACTION;
    INSERT dbo.item_material (material_id, name, unit, material_kind, stock_qty, reserved_qty, safety_stock)
    VALUES ('M0000099', N'测试原料', N'克', 'INGREDIENT', 10.00, 0, 5.00);
    DELETE dbo.item_material WHERE material_id = 'M0000099';

    IF (SELECT COUNT(*) FROM dbo.inv_stock_audit WHERE material_id = 'M0000099' AND change_type = 'DELETE') <> 1
        THROW 51917, N'删除原料后应恰有 1 条 DELETE 审计。', 1;
    IF EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000099' AND change_type = 'DELETE'
          AND (before_qty <> 10 OR after_qty <> 0 OR change_qty <> -10)
    )
        THROW 51918, N'删除审计的前后值/变化量不正确。', 1;
ROLLBACK;
PRINT N'PASS 5 删除记 10 → 0、change_qty=-10';

PRINT N'== 6. 审计：操作人（会话上下文 vs 登录回退） ==';
BEGIN TRANSACTION;
    EXEC sys.sp_set_session_context @key = N'employee_id', @value = N'E0000001';
    UPDATE dbo.item_material SET stock_qty = stock_qty + 1 WHERE material_id = 'M0000001';
    IF NOT EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000001' AND operator_source = 'SESSION' AND operator_id = 'E0000001'
    )
        THROW 51919, N'设置会话上下文后应记录 SESSION/E0000001。', 1;

    EXEC sys.sp_set_session_context @key = N'employee_id', @value = NULL;
    UPDATE dbo.item_material SET stock_qty = stock_qty + 1 WHERE material_id = 'M0000001';
    IF NOT EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000001' AND operator_source = 'LOGIN' AND operator_id IS NULL
    )
        THROW 51920, N'清除会话上下文后应按 LOGIN 回退记录。', 1;
ROLLBACK;
PRINT N'PASS 6 会话上下文 → SESSION/E0000001；无上下文 → LOGIN 回退';

PRINT N'== 7. 审计：role_customer 预占不被审计表权限拦住 ==';
BEGIN TRANSACTION;
    EXECUTE AS USER = 'login_customer';
    UPDATE dbo.item_material SET reserved_qty = reserved_qty + 1 WHERE material_id = 'M0000001';
    REVERT;
    IF EXISTS (SELECT 1 FROM dbo.inv_stock_audit WHERE material_id = 'M0000001')
        THROW 51921, N'顾客预占不应产生审计行。', 1;
ROLLBACK;
PRINT N'PASS 7 role_customer 只改 reserved_qty：放行且 0 审计';

PRINT N'== 7b. 受限账户 login_app / login_stock 写库存：审计权限 + 员工校验 ==';
BEGIN TRANSACTION;
    EXECUTE AS USER = 'login_app';
    EXEC sys.sp_set_session_context @key = N'employee_id', @value = N'E0000001';
    UPDATE dbo.item_material SET stock_qty = stock_qty + 1 WHERE material_id = 'M0000001';
    REVERT;
    IF NOT EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000001' AND operator_source = 'SESSION' AND operator_id = 'E0000001'
          AND change_qty = 1
    )
        THROW 51923, N'login_app 写库存后审计未记录会话员工（检查 staff_employee/audit 授权）。', 1;

    EXECUTE AS USER = 'login_stock';
    EXEC sys.sp_set_session_context @key = N'employee_id', @value = N'E0000003';
    UPDATE dbo.item_material SET stock_qty = stock_qty + 5 WHERE material_id = 'M0000002';
    REVERT;
    IF NOT EXISTS (
        SELECT 1 FROM dbo.inv_stock_audit
        WHERE material_id = 'M0000002' AND operator_source = 'SESSION' AND operator_id = 'E0000003'
          AND change_qty = 5
    )
        THROW 51924, N'login_stock 写库存后审计未记录会话员工。', 1;
ROLLBACK;
PRINT N'PASS 7b 受限账户 login_app / login_stock 写库存成功且审计记 SESSION 员工';

PRINT N'== 8. 审计触发器不重复写业务流水 ==';
BEGIN TRANSACTION;
    DECLARE @log_before INT = (SELECT COUNT(*) FROM dbo.inv_stock_log WHERE material_id = 'M0000001');
    UPDATE dbo.item_material SET stock_qty = stock_qty - 5 WHERE material_id = 'M0000001';
    DECLARE @log_after INT = (SELECT COUNT(*) FROM dbo.inv_stock_log WHERE material_id = 'M0000001');
    IF @log_after <> @log_before
        THROW 51922, N'审计触发器不应写 inv_stock_log。', 1;
ROLLBACK;
PRINT N'PASS 8 审计只写 inv_stock_audit，不新增业务流水';

/* ===========================================================================
   二、订单完成规则触发器
   =========================================================================== */

PRINT N'== 9. 完成：合法 MAKING → COMPLETED ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010001', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    UPDATE dbo.shop_order
    SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
    WHERE order_id = '209901010001';

    IF EXISTS (
        SELECT 1 FROM dbo.shop_order
        WHERE order_id = '209901010001'
          AND (order_status <> 'COMPLETED' OR completed_at IS NULL)
    )
        THROW 51930, N'合法完成应写入 COMPLETED 与 completed_at。', 1;
ROLLBACK;
PRINT N'PASS 9 合法完成：状态与完成时间正确';

PRINT N'== 9b. 完成：role_app（login_app）执行完成通过触发器 ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010001', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    EXECUTE AS USER = 'login_app';
    UPDATE dbo.shop_order
    SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
    WHERE order_id = '209901010001';
    REVERT;
    IF (SELECT order_status FROM dbo.shop_order WHERE order_id = '209901010001') <> 'COMPLETED'
        THROW 51946, N'login_app 完成订单失败（触发器读取所需对象权限不足？）。', 1;
ROLLBACK;
PRINT N'PASS 9b login_app 完成订单：触发器在受限账户下可执行';

PRINT N'== 10. 完成：重复完成被拒（51111） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010002', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
    WHERE order_id = '209901010002';
    BEGIN TRY
        UPDATE dbo.shop_order SET completed_at = '2099-01-01T02:00:00'
        WHERE order_id = '209901010002';
        THROW 51931, N'重复完成未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51111 THROW;
        PRINT N'  caught 51111：' + ERROR_MESSAGE();
    END CATCH;
ROLLBACK;
PRINT N'PASS 10 重复完成（重写 completed_at）→ 51111';

PRINT N'== 10b. 完成：已完成单的 no-op 状态更新放行（权限矩阵用） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010002', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
    WHERE order_id = '209901010002';
    UPDATE dbo.shop_order SET order_status = order_status WHERE order_id = '209901010002';
    IF (SELECT order_status FROM dbo.shop_order WHERE order_id = '209901010002') <> 'COMPLETED'
        THROW 51945, N'no-op 状态更新不应改变订单状态。', 1;
ROLLBACK;
PRINT N'PASS 10b SET order_status=order_status 放行，不误伤权限矩阵';

PRINT N'== 11. 完成：PENDING 直接完成被拒（51112） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010003', 'PENDING', 22.00, 22.00, NULL, NULL, NULL, 1, 1, 0, 0;
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010003';
        THROW 51932, N'PENDING 直接完成未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51112 THROW;
        PRINT N'  caught 51112：' + ERROR_MESSAGE();
    END CATCH;
ROLLBACK;
PRINT N'PASS 11 非 MAKING 来源 → 51112';

PRINT N'== 12. 完成：金额与明细/加料快照不一致被拒（51116） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010004', 'MAKING', 21.00, 21.00, 'CASH', '2099-01-01T00:10:00';
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010004';
        THROW 51933, N'金额不一致未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51116 THROW;
        PRINT N'  caught 51116：' + ERROR_MESSAGE();
    END CATCH;
ROLLBACK;
PRINT N'PASS 12 subtotal ≠ 商品+加料 → 51116';

PRINT N'== 13. 完成：无明细被拒（51114） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010005', 'MAKING', 0.00, 0.00, 'CASH', '2099-01-01T00:10:00',
          NULL, 0, 0, 0, 0;
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010005';
        THROW 51934, N'无明细订单未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51114 THROW;
        PRINT N'  caught 51114：' + ERROR_MESSAGE();
    END CATCH;
ROLLBACK;
PRINT N'PASS 13 无明细 → 51114';

PRINT N'== 14. 完成：无 SALE 扣料证据被拒（51117） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010006', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00',
          NULL, 1, 1, 0, 0;
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010006';
        THROW 51935, N'无 SALE 流水的订单未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51117 THROW;
        PRINT N'  caught 51117：' + ERROR_MESSAGE();
    END CATCH;
ROLLBACK;
PRINT N'PASS 14 无 SALE 扣料 → 51117';

BEGIN TRANSACTION;
    BEGIN TRY
        EXEC #w11_order '209901010006', 'COMPLETED', 22.00, 22.00, 'CASH',
             '2099-01-01T00:10:00', '2099-01-01T01:00:00';
        THROW 51941, N'直接插入已完成单未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51112 THROW;
    END CATCH;
ROLLBACK;
PRINT N'PASS 14a 直接插入已完成单 → 51112';

BEGIN TRANSACTION;
    EXEC #w11_order '209901010006', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    INSERT dbo.inv_stock_log (material_id, log_type, qty, order_id, employee_id, created_at)
    VALUES ('M0000001', 'SALE', 1, '209901010006', 'E0000001', '2099-01-01T00:10:00');
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010006';
        THROW 51942, N'重复 SALE 流水未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51117 THROW;
    END CATCH;
ROLLBACK;
PRINT N'PASS 14b 重复 SALE 流水 → 51117';

BEGIN TRANSACTION;
    EXEC #w11_order '209901010006', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    INSERT dbo.inv_stock_log (material_id, log_type, qty, order_id, employee_id, created_at)
    VALUES ('M0000002', 'SALE', 1, '209901010006', 'E0000001', '2099-01-01T00:10:00');
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010006';
        THROW 51943, N'快照外 SALE 流水未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51117 THROW;
    END CATCH;
ROLLBACK;
PRINT N'PASS 14c 快照外 SALE 流水 → 51117';

PRINT N'== 15. 完成：完成时间缺失被拒（51113） ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010007', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED' WHERE order_id = '209901010007';
        THROW 51936, N'缺 completed_at 未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51113 THROW;
        PRINT N'  caught 51113：' + ERROR_MESSAGE();
    END CATCH;
ROLLBACK;
PRINT N'PASS 15 缺 completed_at → 51113';

PRINT N'== 16. 完成：一条语句同时完成两单 ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010008', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    EXEC #w11_order '209901010009', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:20:00';
    UPDATE dbo.shop_order
    SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
    WHERE order_id IN ('209901010008', '209901010009');

    IF (SELECT COUNT(*) FROM dbo.shop_order
        WHERE order_id IN ('209901010008', '209901010009') AND order_status = 'COMPLETED') <> 2
        THROW 51937, N'两单同时完成应都变为 COMPLETED。', 1;
ROLLBACK;
PRINT N'PASS 16 多行 UPDATE：两单同时完成';

PRINT N'== 17. 完成：合法 + 非法混合，事务不可提交并整体回滚 ==';
-- 这一组先提交两单，再在独立事务里做失败的混合 UPDATE，最后核对两单仍为 MAKING。
BEGIN TRANSACTION;
    EXEC #w11_order '209901010010', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    EXEC #w11_order '209901010011', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00',
          NULL, 0, 0, 0, 0;
COMMIT TRANSACTION;

BEGIN TRANSACTION;
    BEGIN TRY
        UPDATE dbo.shop_order
        SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id IN ('209901010010', '209901010011');
        THROW 51938, N'混合批次未被拒绝。', 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() <> 51114 THROW;
        PRINT N'  caught 51114：' + ERROR_MESSAGE();
        IF XACT_STATE() <> -1
            THROW 51939, N'触发器报错后事务应不可提交（XACT_STATE=-1）。', 1;
    END CATCH;
ROLLBACK TRANSACTION;

IF EXISTS (
    SELECT 1 FROM dbo.shop_order
    WHERE order_id IN ('209901010010', '209901010011') AND order_status = 'COMPLETED'
)
    THROW 51940, N'混合批次失败后出现部分提交（有订单已完成）。', 1;
IF (SELECT COUNT(*) FROM dbo.shop_order
    WHERE order_id IN ('209901010010', '209901010011') AND order_status = 'MAKING') <> 2
    THROW 51941, N'混合批次回滚后两单应仍为 MAKING。', 1;

-- 清理这一组已提交的夹具，保证脚本可重复运行
DELETE dbo.inv_stock_log       WHERE order_id IN ('209901010010', '209901010011');
DELETE dbo.order_material_usage WHERE order_id IN ('209901010010', '209901010011');
DELETE dbo.order_item_addon    WHERE order_id IN ('209901010010', '209901010011');
DELETE dbo.order_item          WHERE order_id IN ('209901010010', '209901010011');
DELETE dbo.shop_order          WHERE order_id IN ('209901010010', '209901010011');
PRINT N'PASS 17 合法+非法混合：事务不可提交、回滚后两单仍 MAKING（无部分提交）';

PRINT N'== 18. 完成：商品改杯型后历史订单仍可完成 ==';
BEGIN TRANSACTION;
    EXEC #w11_order '209901010012', 'MAKING', 22.00, 22.00, 'CASH', '2099-01-01T00:10:00';
    UPDATE dbo.item_product SET size = 'SMALL' WHERE product_id = 'P0000001';
    BEGIN TRY
        UPDATE dbo.shop_order SET order_status = 'COMPLETED', completed_at = '2099-01-01T01:00:00'
        WHERE order_id = '209901010012';
    END TRY
    BEGIN CATCH
        THROW 51943, N'商品改杯型后历史订单被错误拒绝。', 1;
    END CATCH;
    IF NOT EXISTS (
        SELECT 1 FROM dbo.shop_order
        WHERE order_id = '209901010012' AND order_status = 'COMPLETED'
    )
        THROW 51944, N'历史订单应能按明细快照完成。', 1;
ROLLBACK;
PRINT N'PASS 18 历史订单按明细快照完成，不因商品改杯型被拒';

/* --------------------------------------------------------------------------- */
PRINT N'';
PRINT N'ALL TRIGGER TESTS PASSED（18 组主用例，另有 7b/9b/10b/14a/14b/14c 子用例；审计 8 组 / 完成 10 组）';
