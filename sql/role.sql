/* ============================================================================
   role.sql —— 角色与最小权限（第 4 周产出）
   ----------------------------------------------------------------------------
   前置：必须先跑 sql/00-create.sql → 01-schema.sql → constraint.sql → 02-seed.sql。
   本文本的测试要引用 seed 里的真实数据（员工 E0000001 / 原料 M0000001 /
   订单 202609160005 等），故必须在样例数据装载之后执行。

   ★ 角色与 docs/01 §角色 的映射（本库 4 个业务角色，非会员顾客/外卖平台/DBA 不在此列）：
     role_cashier     收银员   —— 点单/收银；不可改库存、券规则、会员余额
     role_maker       制作员   —— 看订单做饮品、改订单状态；不需要会员余额/地址
     role_stock_keeper 库存管理员 —— 管库存/补货/流水；不访问会员资金、不改成交价
     role_manager     店长     —— 维护商品/配方/加料/券规则、审核退款/报损；业务管理 ≠ DBA

   ★ 最小权限原则：只 GRANT 需要的、不 DENY。"越权"= 根本没授权，操作自然被
     拒绝（SELECT 越权报 229，DDL 越权报 262）。这正是"最小权限"的验证方式。

   ★ 本文本【可重复执行】（幂等）：CREATE ROLE/USER 用 IF NOT EXISTS 守卫，
     GRANT 与 ALTER ROLE ... ADD MEMBER 重复执行都是安全的。

   ★ 测试用演示用户 u_* 都是 WITHOUT LOGIN（不真正对应登录名，只在库内存在），
     用 EXECUTE AS USER 切换身份做正/反例，写操作全部事务回滚，不污染 seed 数据。
   ============================================================================ */

SET NOCOUNT ON;

/* 测试会 DML 到 shop_order / member_member / member_balance_log（带筛选唯一索引），
   需 QUOTED_IDENTIFIER / ANSI_NULLS 为 ON；sqlcmd 默认 OFF，必须显式写死。 */
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

USE [MilkTeaShop];
GO

/* ---------------------------------------------------------------------------
   ① 创建 4 个数据库角色（幂等）
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'role_cashier' AND type = 'R')
    CREATE ROLE role_cashier;
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'role_maker' AND type = 'R')
    CREATE ROLE role_maker;
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'role_stock_keeper' AND type = 'R')
    CREATE ROLE role_stock_keeper;
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'role_manager' AND type = 'R')
    CREATE ROLE role_manager;
GO

/* ---------------------------------------------------------------------------
   ② 授权（最小权限）
   --------------------------------------------------------------------------- */

-- 收银员：点单/收银（读商品/配方/加料/会员/券；下单写订单），不碰库存/券规则/会员余额
GRANT SELECT ON dbo.item_product       TO role_cashier;
GRANT SELECT ON dbo.item_recipe        TO role_cashier;
GRANT SELECT ON dbo.item_addon_option  TO role_cashier;
GRANT SELECT ON dbo.member_member      TO role_cashier;   -- 查会员（收银需看余额，但无写权限）
GRANT SELECT ON dbo.mkt_coupon_rule    TO role_cashier;
GRANT SELECT ON dbo.mkt_member_coupon  TO role_cashier;
GRANT SELECT ON dbo.staff_employee     TO role_cashier;
GRANT SELECT, INSERT, UPDATE ON dbo.shop_order       TO role_cashier;
GRANT SELECT, INSERT, UPDATE ON dbo.order_item       TO role_cashier;
GRANT SELECT, INSERT, UPDATE ON dbo.order_item_addon TO role_cashier;

-- 制作员：看订单/配方做饮品，只改订单状态（列级授权），不碰会员余额/地址
GRANT SELECT ON dbo.item_product       TO role_maker;
GRANT SELECT ON dbo.item_recipe        TO role_maker;
GRANT SELECT ON dbo.shop_order         TO role_maker;
GRANT SELECT ON dbo.order_item         TO role_maker;
GRANT SELECT ON dbo.order_item_addon   TO role_maker;
GRANT UPDATE ON dbo.shop_order (order_status) TO role_maker;   -- 只改状态列

-- 库存管理员：管库存/补货/流水；不访问会员资金（member_member 完全不授权），不改商品成交价
GRANT SELECT, UPDATE ON dbo.item_material      TO role_stock_keeper;
GRANT SELECT, INSERT, UPDATE ON dbo.inv_restock      TO role_stock_keeper;
GRANT SELECT, INSERT, UPDATE ON dbo.inv_restock_item TO role_stock_keeper;
GRANT SELECT, INSERT ON dbo.inv_stock_log            TO role_stock_keeper;
GRANT SELECT ON dbo.item_product      TO role_stock_keeper;
GRANT SELECT ON dbo.item_recipe       TO role_stock_keeper;
GRANT SELECT ON dbo.item_addon_option TO role_stock_keeper;

-- 店长：维护商品/配方/加料/券规则，审核退款/报损；业务管理 ≠ DBA（无 DDL、无 GRANT 权限）
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.item_product      TO role_manager;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.item_recipe       TO role_manager;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.item_addon_option TO role_manager;
GRANT SELECT, INSERT, UPDATE, DELETE ON dbo.mkt_coupon_rule   TO role_manager;
GRANT SELECT, UPDATE ON dbo.shop_order        TO role_manager;   -- 审核退款（改状态/退款额）
GRANT SELECT, UPDATE ON dbo.mkt_member_coupon TO role_manager;   -- 退券
GRANT SELECT, INSERT ON dbo.inv_stock_log     TO role_manager;   -- 审核报损写 LOSS 流水
-- 业务管理需要读全量
GRANT SELECT ON dbo.member_member        TO role_manager;
GRANT SELECT ON dbo.staff_employee       TO role_manager;
GRANT SELECT ON dbo.inv_restock          TO role_manager;
GRANT SELECT ON dbo.inv_restock_item     TO role_manager;
GRANT SELECT ON dbo.order_item           TO role_manager;
GRANT SELECT ON dbo.order_item_addon     TO role_manager;
GRANT SELECT ON dbo.order_platform        TO role_manager;
GRANT SELECT ON dbo.member_point_log     TO role_manager;
GRANT SELECT ON dbo.member_balance_log   TO role_manager;
GO

/* ---------------------------------------------------------------------------
   ③ 创建演示用户（WITHOUT LOGIN）并加入角色（幂等）
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'u_cashier')
    CREATE USER u_cashier WITHOUT LOGIN;
ALTER ROLE role_cashier ADD MEMBER u_cashier;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'u_maker')
    CREATE USER u_maker WITHOUT LOGIN;
ALTER ROLE role_maker ADD MEMBER u_maker;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'u_stock_keeper')
    CREATE USER u_stock_keeper WITHOUT LOGIN;
ALTER ROLE role_stock_keeper ADD MEMBER u_stock_keeper;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'u_manager')
    CREATE USER u_manager WITHOUT LOGIN;
ALTER ROLE role_manager ADD MEMBER u_manager;
GO

/* ---------------------------------------------------------------------------
   ④ 正常操作（正例）与越权（反例）测试
   ---------------------------------------------------------------------------
   越权期望错误号：229（缺少 SELECT/INSERT/UPDATE/DELETE 权限）、262（缺少 DDL 权限）。
   每个用例先 EXECUTE AS USER 切换身份，测完 REVERT 回原身份再写结果，
   写操作均在事务里回滚，不污染 seed 数据。
   --------------------------------------------------------------------------- */
CREATE TABLE #results (
    test_id     nvarchar(10)  NOT NULL,
    description nvarchar(250) NOT NULL,
    passed      bit           NOT NULL,
    detail      nvarchar(100) NULL
);
GO

-- P01 收银员能读商品（点单前提）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    SELECT TOP (1) product_id FROM dbo.item_product;
    SET @ok = 1;
END TRY
BEGIN CATCH
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'P01', N'收银员读商品应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P02 收银员能下单（写 shop_order）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    BEGIN TRAN;
    INSERT INTO dbo.shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, created_at)
    VALUES ('202609169999', 'COUNTER', 'COUNTER_CASHIER', 'PENDING', NULL, 'E0000001', 10.00, 0.00, 10.00, NULL, '2026-09-16 10:00:00');
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'P02', N'收银员下单应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P03 制作员能改订单状态（列级授权）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_maker';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.shop_order SET order_status = 'MAKING' WHERE order_id = '202609160005';
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'P03', N'制作员改订单状态应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P04 库存管理员能改库存量
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.item_material SET stock_qty = stock_qty + 1 WHERE material_id = 'M0000001';
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'P04', N'库存管理员改库存量应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P05 库存管理员能记库存流水
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    BEGIN TRAN;
    INSERT INTO dbo.inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at)
    VALUES ('M0000001', 'LOSS', 1, NULL, NULL, 'E0000003', '2026-09-16 10:00:00');
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'P05', N'库存管理员记库存流水应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P06 店长能维护商品（新增商品）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_manager';
BEGIN TRY
    BEGIN TRAN;
    INSERT INTO dbo.item_product (product_id, name, size, unit_price) VALUES ('P9999000', N'测试新品', 'MEDIUM', 9.00);
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'P06', N'店长新增商品应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- N01 收银员改库存应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.item_material SET stock_qty = 0 WHERE material_id = 'M0000001';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N01', N'收银员改库存应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N02 收银员改会员余额应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.member_member SET balance = 999 WHERE member_id = 'C0000001';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N02', N'收银员改会员余额应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N03 制作员读会员表应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_maker';
BEGIN TRY
    SELECT * FROM dbo.member_member;
    SET @ok = 0;
END TRY
BEGIN CATCH
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N03', N'制作员读会员表应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N04 制作员改订单金额（非状态列）应被拒（列级 UPDATE 拒绝报 230，表级才报 229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_maker';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.shop_order SET total_amount = 0 WHERE order_id = '202609160005';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 230 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N04', N'制作员改订单金额应被拒（期望 230）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N05 库存管理员读会员表应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    SELECT * FROM dbo.member_member;
    SET @ok = 0;
END TRY
BEGIN CATCH
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N05', N'库存管理员读会员表应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N06 库存管理员改商品成交价应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.item_product SET unit_price = 1.00 WHERE product_id = 'P0000001';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N06', N'库存管理员改成交价应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N07 店长建表（DDL）应被拒（262）—— 业务管理 ≠ DBA
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_manager';
BEGIN TRY
    CREATE TABLE dbo.__role_test (x int);
    DROP TABLE dbo.__role_test;
    SET @ok = 0;
END TRY
BEGIN CATCH
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 262 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #results VALUES (N'N07', N'店长建表（DDL）应被拒（期望 262）', @ok, CAST(@err AS nvarchar(20)));
GO

/* ---------------------------------------------------------------------------
   ⑤ 结果汇总 + 角色成员清点
   --------------------------------------------------------------------------- */
SELECT
      COUNT(*)                                               AS total_tests
    , SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END)            AS passed_count
    , SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END)            AS failed_count
    , CASE WHEN SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) = 0
             THEN N'ALL TESTS PASSED' ELSE N'HAS FAILURES' END AS result
FROM #results;

SELECT test_id, description, passed, detail
FROM #results
ORDER BY test_id;

DROP TABLE #results;
GO

-- 角色 → 成员清点（作为证据）
SELECT r.name AS role_name, m.name AS member_name
FROM sys.database_role_members rm
JOIN sys.database_principals r ON rm.role_principal_id = r.principal_id
JOIN sys.database_principals m ON rm.member_principal_id = m.principal_id
WHERE r.name LIKE N'role_%'
ORDER BY r.name, m.name;

PRINT N'role.sql: 4 个角色 + 最小权限已就绪，正/反例测试已执行（见上表）。';
GO
