:ON ERROR EXIT
SET XACT_ABORT ON;
IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb', N'MilkTeaShop', N'MilkTeaShop_v01')
    THROW 51030, N'请选择 v1.0 的独立副本作为迁移目标', 1;

SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
BEGIN TRANSACTION;
DECLARE @version NVARCHAR(20);
SELECT @version = CONVERT(NVARCHAR(20), value)
FROM sys.extended_properties WHERE class = 0 AND name = N'milktea_schema_version';

IF COALESCE(@version, N'1.0') NOT IN (N'1.0', N'2.0', N'2.1')
   OR COL_LENGTH('dbo.item_material', 'reserved_qty') IS NULL
   OR NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'role_app')
   OR (SELECT COUNT(*) FROM sys.columns AS c JOIN sys.tables AS t ON t.object_id = c.object_id)
      <> CASE WHEN @version = N'2.1' THEN 114 ELSE 111 END
    THROW 51031, N'迁移目标不符合已知表结构基线。', 1;
GO
:r sql/function.sql
:r sql/procedure.sql

GRANT EXECUTE ON dbo.usp_adjust_stock TO role_app;
GRANT EXECUTE ON dbo.usp_sale_statistics TO role_manager;
GRANT SELECT ON dbo.fn_sales_summary TO role_manager;
GRANT EXECUTE ON dbo.fn_replenish_qty TO role_stock_keeper, role_manager;
GRANT EXECUTE ON dbo.fn_discount TO role_app, role_cashier;
GRANT VIEW DEFINITION ON dbo.usp_adjust_stock TO role_manager;
GRANT VIEW DEFINITION ON dbo.fn_discount TO role_manager;

IF NOT EXISTS (SELECT 1 FROM sys.extended_properties WHERE class = 0 AND name = N'milktea_schema_version')
    EXEC sys.sp_addextendedproperty @name = N'milktea_schema_version', @value = N'2.0';
ELSE IF NOT EXISTS (
    SELECT 1 FROM sys.extended_properties
    WHERE class = 0 AND name = N'milktea_schema_version' AND CONVERT(nvarchar(20), value) IN (N'2.0', N'2.1')
)
    THROW 51032, N'当前数据库版本不能应用此迁移', 1;

COMMIT TRANSACTION;
GO
