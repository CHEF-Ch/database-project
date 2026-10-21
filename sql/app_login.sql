/* ============================================================================
   app_login.sql —— 第七至九周：为 Web 应用创建"受限数据库账户"
   ----------------------------------------------------------------------------
   目标库：MilkTeaShop_v01（v1.0，236 项指纹）
   作用  ：把第 4 周已有的 4 个数据库角色接到真实 SQL 登录上，并补上应用阶段
           才暴露出来的授权缺口；另建"顾客"角色与 role_app 写事务账户。

   与 role.sql 的关系：
     · role.sql 作用于 v0.1（USE [MilkTeaShop]），是第 4 周的证据，保持不动；
     · 本脚本只作用于 v1.0 副本，属于第七至九周的增量。

   幂等：可重复执行。CREATE LOGIN / CREATE USER 有存在性判断，GRANT 可重复，
         ALTER ROLE ... ADD MEMBER 对已是成员的用户重复执行为无操作。

   执行顺序：先校验（库 + 4 个角色），校验不过直接 THROW，不留下半成品登录；
             校验通过后才创建登录与用户。

   ⚠ 前置：SQL 登录要能用于连接认证，实例必须是"混合模式"。
     若仍是仅 Windows 模式，先运行：scripts/enable-mixed-auth.ps1
     （本脚本在仅 Windows 模式下也能执行成功，只是登录暂时无法用于连接。）

   用法：
     sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/app_login.sql
   ============================================================================ */

SET NOCOUNT ON;
GO

/* ---------------------------------------------------------------------------
   ① 前置校验：目标库与 4 个业务角色必须已存在
      （角色由 v0.1 的 role.sql 创建，随备份/还原继承到副本）
   --------------------------------------------------------------------------- */
IF DB_ID(N'MilkTeaShop_v01') IS NULL
    THROW 51000, N'目标库 MilkTeaShop_v01 不存在：请先按 sql/README.md 复现 v0.1 并迁移为 v1.0。', 1;
GO

USE [MilkTeaShop_v01];
GO

IF DATABASE_PRINCIPAL_ID(N'role_cashier') IS NULL
    THROW 51001, N'缺少角色 role_cashier：请在源库 MilkTeaShop 上执行 sql/role.sql 后重新备份/迁移。', 1;
IF DATABASE_PRINCIPAL_ID(N'role_maker') IS NULL
    THROW 51002, N'缺少角色 role_maker：请在源库上执行 sql/role.sql 后重新备份/迁移。', 1;
IF DATABASE_PRINCIPAL_ID(N'role_stock_keeper') IS NULL
    THROW 51003, N'缺少角色 role_stock_keeper：请在源库上执行 sql/role.sql 后重新备份/迁移。', 1;
IF DATABASE_PRINCIPAL_ID(N'role_manager') IS NULL
    THROW 51004, N'缺少角色 role_manager：请在源库上执行 sql/role.sql 后重新备份/迁移。', 1;
GO

/* ---------------------------------------------------------------------------
   ② 创建 6 个 SQL 登录（5 个业务角色 + login_app 写事务账户；示例密码）
   --------------------------------------------------------------------------- */
USE master;
GO

IF SUSER_ID(N'login_clerk') IS NULL
    CREATE LOGIN [login_clerk] WITH PASSWORD = N'Clerk#2026',
        CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [MilkTeaShop_v01];
IF SUSER_ID(N'login_customer') IS NULL
    CREATE LOGIN [login_customer] WITH PASSWORD = N'Cust#2026',
        CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [MilkTeaShop_v01];
IF SUSER_ID(N'login_maker') IS NULL
    CREATE LOGIN [login_maker] WITH PASSWORD = N'Maker#2026',
        CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [MilkTeaShop_v01];
IF SUSER_ID(N'login_stock') IS NULL
    CREATE LOGIN [login_stock] WITH PASSWORD = N'Stock#2026',
        CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [MilkTeaShop_v01];
IF SUSER_ID(N'login_manager') IS NULL
    CREATE LOGIN [login_manager] WITH PASSWORD = N'Mgr#2026',
        CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [MilkTeaShop_v01];
IF SUSER_ID(N'login_app') IS NULL
    CREATE LOGIN [login_app] WITH PASSWORD = N'App#2026',
        CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF, DEFAULT_DATABASE = [MilkTeaShop_v01];
PRINT N'② 6 个登录已就绪（5 个业务角色 + login_app 写事务账户）';
GO

USE [MilkTeaShop_v01];
GO

/* ---------------------------------------------------------------------------
   ③ 建库用户并加入对应角色
   --------------------------------------------------------------------------- */
IF USER_ID(N'login_clerk') IS NULL    CREATE USER [login_clerk]    FOR LOGIN [login_clerk];
IF USER_ID(N'login_maker') IS NULL    CREATE USER [login_maker]    FOR LOGIN [login_maker];
IF USER_ID(N'login_stock') IS NULL    CREATE USER [login_stock]    FOR LOGIN [login_stock];
IF USER_ID(N'login_manager') IS NULL  CREATE USER [login_manager]  FOR LOGIN [login_manager];
IF USER_ID(N'login_customer') IS NULL CREATE USER [login_customer] FOR LOGIN [login_customer];
IF USER_ID(N'login_app') IS NULL      CREATE USER [login_app]      FOR LOGIN [login_app];
GO

IF DATABASE_PRINCIPAL_ID(N'role_app') IS NULL CREATE ROLE [role_app];
GO

ALTER ROLE [role_cashier]      ADD MEMBER [login_clerk];
ALTER ROLE [role_maker]        ADD MEMBER [login_maker];
ALTER ROLE [role_stock_keeper] ADD MEMBER [login_stock];
ALTER ROLE [role_manager]      ADD MEMBER [login_manager];
ALTER ROLE [role_app]          ADD MEMBER [login_app];
GO

/* ---------------------------------------------------------------------------
   ④ 顾客角色（第 4 周没有；应用要求"至少店员 + 顾客"）
      读：菜单类表 + 订单类表。
      写：**可自助下单** —— 写订单头/明细/加料，并把原料的 reserved_qty（预占）加回去。
      ⚠ 顾客对 item_material 只有**列级** UPDATE(reserved_qty)，没有 stock_qty/
         safety_stock；行级隔离（只能改自己那几行预占）仍靠应用参数化，RLS 留 v2.0。
      ⚠ 订单与会员是"表级 SELECT"，行级过滤（只看自己的单）在应用层做。
   --------------------------------------------------------------------------- */
IF DATABASE_PRINCIPAL_ID(N'role_customer') IS NULL
    CREATE ROLE [role_customer];
GO

GRANT SELECT ON dbo.item_product       TO role_customer;   -- 菜单：商品与价格
GRANT SELECT ON dbo.item_recipe        TO role_customer;   -- 菜单：配方（过敏原展示）
GRANT SELECT ON dbo.item_addon_option  TO role_customer;   -- 菜单：可选加料
GRANT SELECT ON dbo.mkt_coupon_rule    TO role_customer;   -- 优惠活动
GRANT SELECT ON dbo.shop_order         TO role_customer;   -- 我的订单（应用层按会员过滤）
GRANT SELECT ON dbo.order_item         TO role_customer;
GRANT SELECT ON dbo.order_item_addon   TO role_customer;
GRANT SELECT ON dbo.member_member      TO role_customer;   -- 自己的会员信息（应用层过滤）
GO

-- 自助点单：写订单三表 + 读库存 + 只改预占量
GRANT INSERT ON dbo.shop_order       TO role_customer;
GRANT INSERT ON dbo.order_item       TO role_customer;
GRANT INSERT ON dbo.order_item_addon TO role_customer;
GRANT SELECT ON dbo.item_material    TO role_customer;
GRANT UPDATE ON dbo.item_material (reserved_qty) TO role_customer;
GO

PRINT N'④ 顾客角色：可读菜单/订单，**可自助下单**（写订单三表 + 只改 reserved_qty）';
GO

ALTER ROLE [role_customer] ADD MEMBER [login_customer];
GO

/* ---------------------------------------------------------------------------
   ⑤ 补齐第 4 周留下的 3 处授权缺口（应用阶段才暴露）
      a) 收银员下单要做"库存充足校验"，但 role_cashier 没有 item_material 的 SELECT
         —— 实测会报 Msg 229。补 SELECT（仍不能 UPDATE，改库存归 role_stock_keeper）。
      b) 制作员看订单要确认加料，但 role_maker 没有 item_addon_option 的 SELECT。
      c) 店长要查看库存（权限矩阵里的 inventory.read），但 role_manager 没有
         item_material 的 SELECT —— 实测 Msg 229。
         ⚠ 这一条是复核时补的：早期版本用 GRANT VIEW DEFINITION 让 /health 数到 236，
            但那只是"看得到元数据"，店长实际读库存仍会 229，等于用元数据权限
            掩盖了功能缺口。现在改为补足 SELECT，VIEW DEFINITION 不再需要。
   --------------------------------------------------------------------------- */
GRANT SELECT ON dbo.item_material      TO role_cashier;
GRANT SELECT ON dbo.item_addon_option  TO role_maker;
GRANT SELECT ON dbo.item_material      TO role_manager;
GO

PRINT N'⑤ 已补齐 3 处授权缺口：role_cashier(item_material)、role_maker(item_addon_option)、role_manager(item_material)';
GO

/* ---------------------------------------------------------------------------
   ⑥ 统计视图授权（否则 DB_AUTH=sql 下这些视图全部 229）
      · 销量/日结：店员与店长可见
      · 会员消费汇总：仅店长
      · 原料临期：库存管理员与店长
   --------------------------------------------------------------------------- */
GRANT SELECT ON dbo.v_product_sales_summary TO role_cashier;
GRANT SELECT ON dbo.v_daily_order_summary   TO role_cashier;
GRANT SELECT ON dbo.v_product_sales_summary TO role_manager;
GRANT SELECT ON dbo.v_daily_order_summary   TO role_manager;
GRANT SELECT ON dbo.v_member_order_summary  TO role_manager;
GRANT SELECT ON dbo.v_material_expiry_alert TO role_manager;
GRANT SELECT ON dbo.v_material_expiry_alert TO role_stock_keeper;
GO

PRINT N'⑥ 已授予统计视图权限（店员 2 个、店长 4 个、库存 1 个）';
GO

/* ---------------------------------------------------------------------------
   ⑦ 收窄店员对 shop_order 的 UPDATE 到“下单/改单/支付/完成”真正需要的列
      理由：docs/01 规定“审核退款”是店长职责；role.sql（第 4 周）给的是**整表**
            UPDATE，店员因此能直接写 refund_amount / refund_at / member_id /
            employee_id / created_at，与业务规则冲突。
      v1.0 应用阶段把店员收窄到列级；**店长保持整表**（其职责含退款审核与异常修正）。
      影响：role.sql 的 13 项测试里 u_cashier 只有 INSERT 订单、改库存(229)、
            改会员(229)，**没有一条是 UPDATE 订单**，故不受影响（已逐条核对）。
   --------------------------------------------------------------------------- */
REVOKE UPDATE ON dbo.shop_order FROM role_cashier;
GRANT UPDATE ON dbo.shop_order
      (order_status, pay_method, pay_txn_no, paid_at, completed_at,
       item_subtotal, coupon_discount, total_amount)
  TO role_cashier;
GO

PRINT N'⑦ 已把 role_cashier 对 shop_order 的 UPDATE 收窄为 8 列（退款额/会员/经办人等仍仅店长可写）';
GO

/* ---------------------------------------------------------------------------
   ⑧ 应用写路径授权补缺（2026-10-21 交叉复核）
      背景：DAO 的真实语句比第 4 周的单表测试更宽：
        · maker 看订单详情要 JOIN item_material，完成订单还要写 completed_at；
        · cashier 下单/支付要在同一事务里预占/扣料、写库存流水、扣余额/积分、核销券；
        · manager 退款要写余额/积分流水。
      原则：仍按角色职责授权，不创建 dbo 级“万能账户”。
      注：顾客连接只负责“下单 + 预占”；支付确认/扣料由 clerk/系统连接执行，
          与 docs/09 §10“顾客写权限被切成只能下单”口径一致。
   --------------------------------------------------------------------------- */
-- 制作员：读加料原料名；完成订单写 completed_at
GRANT SELECT ON dbo.item_material TO role_maker;
GRANT SELECT ON dbo.order_platform TO role_maker;
GRANT UPDATE ON dbo.shop_order (completed_at) TO role_maker;

-- 店员：读平台单详情需要 order_platform；库存/流水写权限不放给 role_cashier，
-- 以免违背“店员不能调库存”的权限矩阵；订单写事务统一走 role_app。
-- 先撤销早期版本误授给 cashier 的写权限（可重复执行）。
REVOKE UPDATE ON dbo.item_material FROM role_cashier;
REVOKE SELECT, INSERT ON dbo.inv_stock_log FROM role_cashier;
REVOKE UPDATE ON dbo.member_member FROM role_cashier;
REVOKE SELECT, INSERT ON dbo.member_point_log FROM role_cashier;
REVOKE SELECT, INSERT ON dbo.member_balance_log FROM role_cashier;
REVOKE UPDATE ON dbo.mkt_member_coupon FROM role_cashier;
REVOKE INSERT ON dbo.order_platform FROM role_cashier;
GRANT SELECT ON dbo.order_platform TO role_cashier;

-- 店长：退款写余额/积分流水
GRANT UPDATE ON dbo.member_member (points, balance) TO role_manager;
GRANT INSERT ON dbo.member_point_log TO role_manager;
GRANT INSERT ON dbo.member_balance_log TO role_manager;

-- 应用写事务账户 role_app：只授予“下单/支付/扣料/积分/余额/券”所需 DML，
-- 不给 DDL/GRANT/DELETE；应用层仍按 session 角色做权限矩阵校验。
GRANT SELECT ON dbo.item_product      TO role_app;
GRANT SELECT ON dbo.item_material     TO role_app;
GRANT SELECT ON dbo.item_recipe       TO role_app;
GRANT SELECT ON dbo.item_addon_option TO role_app;
GRANT SELECT ON dbo.member_member     TO role_app;
GRANT SELECT ON dbo.mkt_coupon_rule   TO role_app;
GRANT SELECT ON dbo.mkt_member_coupon TO role_app;
GRANT SELECT ON dbo.shop_order        TO role_app;
GRANT SELECT ON dbo.order_item        TO role_app;
GRANT SELECT ON dbo.order_item_addon  TO role_app;
GRANT SELECT ON dbo.order_platform    TO role_app;
GRANT SELECT ON dbo.inv_stock_log     TO role_app;
GRANT SELECT ON dbo.member_point_log  TO role_app;
GRANT SELECT ON dbo.member_balance_log TO role_app;
GRANT SELECT ON dbo.v_product_sales_summary TO role_app;
GRANT SELECT ON dbo.v_daily_order_summary   TO role_app;
GRANT SELECT ON dbo.v_member_order_summary  TO role_app;

GRANT INSERT, UPDATE ON dbo.shop_order       TO role_app;
GRANT INSERT, UPDATE ON dbo.order_item       TO role_app;
GRANT INSERT, UPDATE ON dbo.order_item_addon TO role_app;
GRANT INSERT ON dbo.order_platform           TO role_app;
GRANT UPDATE ON dbo.item_material            TO role_app;
GRANT INSERT ON dbo.inv_stock_log            TO role_app;
GRANT UPDATE ON dbo.member_member            TO role_app;
GRANT INSERT ON dbo.member_point_log         TO role_app;
GRANT INSERT ON dbo.member_balance_log       TO role_app;
GRANT UPDATE ON dbo.mkt_member_coupon        TO role_app;
GO

PRINT N'⑧ 已补应用写路径授权：maker(读原料/平台 + 写 completed_at)、cashier(读平台单)、manager(余额/积分流水)、role_app(订单/库存/流水/会员/券写事务)';
GO

/* ---------------------------------------------------------------------------
   ⑨ 校验：登录 ↔ 库用户 ↔ 角色
   --------------------------------------------------------------------------- */
PRINT N'================================================================================';
PRINT N'校验 1：登录 → 库用户 → 角色';
PRINT N'================================================================================';
SELECT  sp.name             AS login_name
      , dp.name            AS db_user
      , r.name             AS role_name
      , sp.is_disabled     AS login_disabled
FROM sys.database_principals dp
JOIN sys.database_role_members rm ON rm.member_principal_id = dp.principal_id
JOIN sys.database_principals   r  ON r.principal_id        = rm.role_principal_id
LEFT JOIN sys.server_principals sp ON sp.name = dp.name
WHERE dp.name LIKE N'login[_]%'
ORDER BY r.name, sp.name;
GO

PRINT N'================================================================================';
PRINT N'校验 2：各角色的关键对象权限（应有 item_material 的是 cashier/maker/manager/role_app）';
PRINT N'================================================================================';
SELECT  OBJECT_NAME(p.major_id) AS object_name
      , r.name                  AS role_name
      , p.permission_name
FROM sys.database_permissions p
JOIN sys.database_principals r ON r.principal_id = p.grantee_principal_id
WHERE r.name IN (N'role_cashier', N'role_maker', N'role_manager', N'role_stock_keeper',
                 N'role_customer', N'role_app')
  AND OBJECT_NAME(p.major_id) IN (N'item_material', N'item_addon_option',
                                  N'v_product_sales_summary', N'v_daily_order_summary',
                                  N'v_member_order_summary', N'v_material_expiry_alert')
ORDER BY role_name, object_name;
GO

PRINT N'================================================================================';
PRINT N'校验 3：shop_order 上的 UPDATE 粒度（店员 8 列、制作员 2 列、店长/role_app 整表）';
PRINT N'================================================================================';
SELECT  r.name AS role_name
      , CASE WHEN p.minor_id = 0 THEN N'(整表)' ELSE COL_NAME(p.major_id, p.minor_id) END AS column_name
FROM sys.database_permissions p
JOIN sys.database_principals r ON r.principal_id = p.grantee_principal_id
WHERE p.major_id = OBJECT_ID('dbo.shop_order')
  AND p.permission_name = N'UPDATE'
  AND r.name IN (N'role_cashier', N'role_maker', N'role_manager', N'role_app')
ORDER BY r.name, column_name;
GO

PRINT N'================================================================================';
PRINT N'校验 4：实例是否已开混合模式（0 = 已开，1 = 仍仅 Windows；本脚本创建登录不受影响）';
PRINT N'================================================================================';
SELECT CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS int) AS windows_only;
GO
