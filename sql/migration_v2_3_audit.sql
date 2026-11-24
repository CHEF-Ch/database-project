:ON ERROR EXIT
/* ============================================================================
   sql/migration_v2_3_audit.sql —— 第十一周 v2.2 → v2.3
   ----------------------------------------------------------------------------
   · 新建库存审计表 dbo.inv_stock_audit（与业务流水 inv_stock_log 分离）
   · 部署两个触发器（:r sql/trigger.sql）
   · 授权：role_app / role_stock_keeper 写审计，role_manager / role_stock_keeper 读审计
   · 版本标记 2.3；同时把窗口查询函数部署到同一个副本

   前置：v2.2（含 order_material_usage / app_user / role_app）。
   目标指纹：131 列 / 20 主码 / 3 UQ + 4 筛选唯一索引 / 29 外码 / 66 CHECK /
             18 DEFAULT = 271（见 app/dao/connection.py 的 V23_SCHEMA_PARTS）。
   ============================================================================ */
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;

IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb', N'MilkTeaShop', N'MilkTeaShop_v01')
    THROW 51050, N'请选择独立的迁移副本（v2.2 验收库），不要对源库执行。', 1;

BEGIN TRANSACTION;

DECLARE @version NVARCHAR(20);
SELECT @version = CONVERT(NVARCHAR(20), value)
FROM sys.extended_properties
WHERE class = 0 AND name = N'milktea_schema_version';

IF @version IS NULL OR @version NOT IN (N'2.2', N'2.3')
    THROW 51051, N'请先把副本升级到 v2.2（v2.0 → v2.1 耗用快照 → v2.2 账号表）。', 1;

IF OBJECT_ID('dbo.order_material_usage', 'U') IS NULL
    THROW 51052, N'缺少 v2.1 耗用快照表，订单完成规则无法核对扣料。', 1;

IF OBJECT_ID('dbo.app_user', 'U') IS NULL
    THROW 51053, N'缺少 v2.2 账号表，请先执行 migration_v2_2_app_user.sql。', 1;

IF OBJECT_ID('dbo.inv_stock_audit', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.inv_stock_audit
    (
        audit_id        BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_inv_stock_audit PRIMARY KEY,
        material_id     CHAR(8)       NOT NULL,
        before_qty      DECIMAL(10,2) NOT NULL,
        after_qty       DECIMAL(10,2) NOT NULL,
        change_qty      AS (after_qty - before_qty) PERSISTED,
        change_type     VARCHAR(10)   NOT NULL
            CONSTRAINT CK_inv_stock_audit_change_type CHECK (change_type IN ('INSERT', 'UPDATE', 'DELETE')),
        operator_id     VARCHAR(128)  NULL,
        operator_source VARCHAR(10)   NOT NULL
            CONSTRAINT CK_inv_stock_audit_operator_source CHECK (operator_source IN ('SESSION', 'LOGIN')),
        login_name      NVARCHAR(128) NOT NULL,
        changed_at      DATETIME2(0)  NOT NULL
            CONSTRAINT DF_inv_stock_audit_changed_at DEFAULT (SYSDATETIME()),
        CONSTRAINT CK_inv_stock_audit_qty CHECK (before_qty >= 0 AND after_qty >= 0)
    );
END;

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.inv_stock_audit')
      AND name = N'IX_inv_stock_audit_material_time'
)
    CREATE INDEX IX_inv_stock_audit_material_time
        ON dbo.inv_stock_audit (material_id, changed_at DESC);

-- 审计表不建外码：主数据删除后历史审计仍须保留原始编号（设计文档 §二/§七）。
:r sql/trigger.sql
:r sql/window_query.sql

IF OBJECT_ID('dbo.trg_item_material_audit', 'TR') IS NULL
    THROW 51054, N'库存审计触发器部署失败。', 1;
IF OBJECT_ID('dbo.trg_shop_order_complete', 'TR') IS NULL
    THROW 51055, N'订单完成规则触发器部署失败。', 1;

IF DATABASE_PRINCIPAL_ID(N'role_app') IS NOT NULL
BEGIN
    GRANT SELECT, INSERT ON dbo.inv_stock_audit TO role_app;
END;
IF DATABASE_PRINCIPAL_ID(N'role_stock_keeper') IS NOT NULL
BEGIN
    GRANT SELECT, INSERT ON dbo.inv_stock_audit TO role_stock_keeper;
END;
IF DATABASE_PRINCIPAL_ID(N'role_manager') IS NOT NULL
BEGIN
    GRANT SELECT ON dbo.inv_stock_audit TO role_manager;
END;

-- 审计触发器要用会话上下文里的员工编号校验 staff_employee；role_app/role_stock_keeper
-- 是两条真实写库存的账户，缺少该 SELECT 会让每次库存写入在 SQL 认证下报 229。
IF DATABASE_PRINCIPAL_ID(N'role_app') IS NOT NULL
    GRANT SELECT ON dbo.staff_employee TO role_app;
IF DATABASE_PRINCIPAL_ID(N'role_stock_keeper') IS NOT NULL
    GRANT SELECT ON dbo.staff_employee TO role_stock_keeper;

EXEC sys.sp_updateextendedproperty
     @name = N'milktea_schema_version',
     @value = N'2.3';

COMMIT TRANSACTION;
GO

PRINT N'migration_v2_3_audit: inv_stock_audit + 两个触发器 + 三个窗口查询函数已部署（v2.3）。';
GO
