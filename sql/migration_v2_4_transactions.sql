:ON ERROR EXIT
-- 从仓库根目录运行。业务过程、权限和版本标记一起提交。
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
IF COALESCE((SELECT CONVERT(VARCHAR(10), value) FROM sys.extended_properties
             WHERE class = 0 AND name = N'milktea_schema_version'), '') NOT IN ('2.3', '2.4')
    THROW 51200, N'请先部署 v2.3。', 1;
BEGIN TRANSACTION;
GO
:r sql/order_transaction.sql
:r sql/restock_transaction.sql
-- /health 使用经理登录，只需读取过程元数据。
GRANT VIEW DEFINITION ON dbo.usp_complete_order TO role_manager;
GRANT VIEW DEFINITION ON dbo.usp_receive_restock TO role_manager;
EXEC sys.sp_updateextendedproperty @name = N'milktea_schema_version', @value = N'2.4';
COMMIT TRANSACTION;
GO
