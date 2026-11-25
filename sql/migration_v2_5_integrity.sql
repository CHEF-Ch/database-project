:ON ERROR EXIT
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
IF COALESCE((SELECT CONVERT(VARCHAR(10), value) FROM sys.extended_properties
             WHERE class = 0 AND name = N'milktea_schema_version'), '') NOT IN ('2.4', '2.5')
    THROW 51300, N'请先部署 v2.4。', 1;
BEGIN TRANSACTION;

IF EXISTS (
    SELECT 1 FROM dbo.item_addon_option AS a
    JOIN dbo.item_material AS m ON m.material_id = a.material_id
    WHERE m.material_kind <> 'ADDON'
)
    THROW 51301, N'已有加料配置引用非加料原料，请核对后再迁移。', 1;

IF OBJECT_ID('dbo.mkt_coupon_order_event', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.mkt_coupon_order_event (
        event_id BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        member_coupon_id BIGINT NOT NULL,
        member_id CHAR(8) NOT NULL,
        coupon_rule_id CHAR(8) NOT NULL,
        order_id CHAR(12) NOT NULL,
        event_type VARCHAR(10) NOT NULL CHECK (event_type IN ('LOCKED', 'USED', 'RETURNED', 'BASELINE')),
        occurred_at DATETIME2(0) NOT NULL DEFAULT SYSUTCDATETIME()
    );
    CREATE INDEX IX_coupon_order_event_order ON dbo.mkt_coupon_order_event (order_id, event_id);

    -- 只保存仍能确认的关联。BASELINE 表示观察到已有记录，时间为迁移时刻。
    INSERT dbo.mkt_coupon_order_event (member_coupon_id, member_id, coupon_rule_id, order_id, event_type)
    SELECT member_coupon_id, member_id, coupon_rule_id, used_order_id, 'BASELINE'
    FROM dbo.mkt_member_coupon WHERE used_order_id IS NOT NULL;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_addon_option_material
ON dbo.item_addon_option
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (
        SELECT 1 FROM inserted AS i
        JOIN dbo.item_material AS m WITH (UPDLOCK, HOLDLOCK) ON m.material_id = i.material_id
        WHERE m.material_kind <> 'ADDON'
    )
        THROW 51302, N'选项必须引用加料类别原料。', 1;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_material_addon_category
ON dbo.item_material
AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT UPDATE(material_kind) RETURN;
    IF EXISTS (
        SELECT 1 FROM inserted AS i JOIN deleted AS d ON d.material_id = i.material_id
        JOIN dbo.item_addon_option AS a WITH (UPDLOCK, HOLDLOCK) ON a.material_id = i.material_id
        WHERE d.material_kind = 'ADDON' AND i.material_kind <> 'ADDON'
    )
        THROW 51303, N'该加料仍有选项引用，不能变更原料类别。', 1;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_order_item_catalog_snapshot
ON dbo.order_item
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (
        SELECT 1 FROM inserted AS i
        LEFT JOIN deleted AS d ON d.order_id = i.order_id AND d.item_line = i.item_line
        JOIN dbo.item_product AS p WITH (HOLDLOCK) ON p.product_id = i.product_id
        WHERE (d.order_id IS NULL OR d.product_id <> i.product_id OR d.size <> i.size)
          AND (i.size <> p.size OR EXISTS (
              SELECT 1 FROM dbo.order_item_addon AS a
              WHERE a.order_id = i.order_id AND a.item_line = i.item_line
                AND NOT EXISTS (
                    SELECT 1 FROM dbo.item_addon_option AS opt WITH (HOLDLOCK)
                    WHERE opt.product_id = i.product_id AND opt.material_id = a.material_id
                )
          ))
    )
        THROW 51304, N'订单明细杯型或加料归属与商品配置不一致。', 1;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_order_addon_catalog_snapshot
ON dbo.order_item_addon
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS (
        SELECT 1 FROM inserted AS i
        JOIN dbo.order_item AS line WITH (HOLDLOCK)
          ON line.order_id = i.order_id AND line.item_line = i.item_line
        LEFT JOIN deleted AS d ON d.order_id = i.order_id AND d.item_line = i.item_line
                             AND d.material_id = i.material_id
        WHERE d.order_id IS NULL AND NOT EXISTS (
            SELECT 1 FROM dbo.item_addon_option AS opt WITH (HOLDLOCK)
            WHERE opt.product_id = line.product_id AND opt.material_id = i.material_id
        )
    )
        THROW 51305, N'该加料不属于订单商品的可选配置。', 1;
END;
GO

CREATE OR ALTER TRIGGER dbo.trg_member_coupon_order_event
ON dbo.mkt_member_coupon
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;
    INSERT dbo.mkt_coupon_order_event (member_coupon_id, member_id, coupon_rule_id, order_id, event_type)
    SELECT d.member_coupon_id, d.member_id, d.coupon_rule_id, d.used_order_id, 'RETURNED'
    FROM inserted AS i JOIN deleted AS d ON d.member_coupon_id = i.member_coupon_id
    WHERE d.used_order_id IS NOT NULL AND d.coupon_status IN ('LOCKED', 'USED')
      AND (i.used_order_id IS NULL OR i.used_order_id <> d.used_order_id OR i.coupon_status = 'RETURNED');

    INSERT dbo.mkt_coupon_order_event (member_coupon_id, member_id, coupon_rule_id, order_id, event_type)
    SELECT i.member_coupon_id, i.member_id, i.coupon_rule_id, i.used_order_id, i.coupon_status
    FROM inserted AS i LEFT JOIN deleted AS d ON d.member_coupon_id = i.member_coupon_id
    WHERE i.used_order_id IS NOT NULL AND i.coupon_status IN ('LOCKED', 'USED')
      AND (d.used_order_id IS NULL OR d.used_order_id <> i.used_order_id OR d.coupon_status <> i.coupon_status);
END;
GO

GRANT SELECT ON dbo.mkt_coupon_order_event TO role_manager;
GRANT SELECT ON dbo.fn_product_sales_rank TO role_manager;
GRANT SELECT ON dbo.fn_daily_sales_cumulative TO role_manager;
GRANT SELECT ON dbo.fn_daily_sales_moving_avg TO role_manager;
EXEC sys.sp_updateextendedproperty @name=N'milktea_schema_version', @value=N'2.5';
COMMIT TRANSACTION;
GO
