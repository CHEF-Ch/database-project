:ON ERROR EXIT
SET XACT_ABORT ON;
IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb', N'MilkTeaShop', N'MilkTeaShop_v01')
    THROW 51030, N'请选择独立的迁移副本。', 1;
BEGIN TRANSACTION;

DECLARE @version nvarchar(20);
SELECT @version = CONVERT(nvarchar(20), value)
FROM sys.extended_properties WHERE class = 0 AND name = N'milktea_schema_version';
IF @version IS NULL OR @version NOT IN (N'2.0', N'2.1')
    THROW 51033, 'Install v2.0 on the target copy before the usage migration.', 1;

IF @version = N'2.0'
BEGIN
    CREATE TABLE dbo.order_material_usage (
        order_id CHAR(12) NOT NULL,
        material_id CHAR(8) NOT NULL,
        qty DECIMAL(10,2) NOT NULL,
        CONSTRAINT PK_order_material_usage PRIMARY KEY (order_id, material_id),
        CONSTRAINT FK_order_material_usage_order FOREIGN KEY (order_id) REFERENCES dbo.shop_order (order_id),
        CONSTRAINT FK_order_material_usage_material FOREIGN KEY (material_id) REFERENCES dbo.item_material (material_id),
        CONSTRAINT CK_order_material_usage_qty CHECK (qty > 0)
    );
    GRANT SELECT, INSERT ON dbo.order_material_usage TO role_app;
    GRANT SELECT ON dbo.order_material_usage TO role_manager;

    EXEC sys.sp_updateextendedproperty @name = N'milktea_schema_version', @value = N'2.1';
END;

IF OBJECT_ID('dbo.order_material_usage', 'U') IS NULL
    THROW 51034, 'Usage snapshot table is missing.', 1;

COMMIT TRANSACTION;
GO
