SET NOCOUNT ON;

IF NOT EXISTS (
    SELECT 1 FROM sys.extended_properties
    WHERE class = 0 AND name = N'milktea_schema_version'
      AND CONVERT(nvarchar(20), value) = N'2.0'
)
    THROW 51110, 'Missing v2 migration marker', 1;

IF OBJECT_ID('dbo.usp_adjust_stock', 'P') IS NULL
   OR OBJECT_ID('dbo.usp_sale_statistics', 'P') IS NULL
   OR OBJECT_ID('dbo.fn_discount', 'FN') IS NULL
   OR OBJECT_ID('dbo.fn_replenish_qty', 'FN') IS NULL
   OR OBJECT_ID('dbo.fn_sales_summary', 'IF') IS NULL
    THROW 51111, 'Missing v2 object', 1;

EXECUTE AS USER = 'login_manager';
DECLARE @report BIT = ISNULL(HAS_PERMS_BY_NAME('dbo.usp_sale_statistics', 'OBJECT', 'EXECUTE'), 0),
        @manager_adjust BIT = ISNULL(HAS_PERMS_BY_NAME('dbo.usp_adjust_stock', 'OBJECT', 'EXECUTE'), 0);
REVERT;
EXECUTE AS USER = 'login_app';
DECLARE @app_adjust BIT = ISNULL(HAS_PERMS_BY_NAME('dbo.usp_adjust_stock', 'OBJECT', 'EXECUTE'), 0);
REVERT;

IF @report <> 1 OR @manager_adjust <> 0 OR @app_adjust <> 1
    THROW 51112, 'Unexpected v2 object permissions', 1;

PRINT 'PASS: v2 migration marker, object inventory and permissions';
