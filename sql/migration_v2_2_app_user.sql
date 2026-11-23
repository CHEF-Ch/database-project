:ON ERROR EXIT
SET XACT_ABORT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;

IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb', N'MilkTeaShop', N'MilkTeaShop_v01')
    THROW 51040, N'请选择独立的迁移副本。', 1;

BEGIN TRANSACTION;
DECLARE @version NVARCHAR(20);
SELECT @version = CONVERT(NVARCHAR(20), value)
FROM sys.extended_properties WHERE class = 0 AND name = N'milktea_schema_version';
IF @version IS NULL OR @version NOT IN (N'2.1', N'2.2')
    THROW 51041, N'请先部署 v2.1。', 1;

IF @version = N'2.1'
BEGIN
    CREATE TABLE dbo.app_user (
        username NVARCHAR(30) COLLATE Latin1_General_100_BIN2 NOT NULL
            CONSTRAINT PK_app_user PRIMARY KEY,
        password_hash VARCHAR(512) NOT NULL,
        role_code VARCHAR(10) NOT NULL,
        member_id CHAR(8) NULL CONSTRAINT FK_app_user_member REFERENCES dbo.member_member(member_id),
        employee_id CHAR(8) NULL CONSTRAINT FK_app_user_employee REFERENCES dbo.staff_employee(employee_id),
        display_name NVARCHAR(100) NOT NULL,
        is_active BIT NOT NULL CONSTRAINT DF_app_user_is_active DEFAULT (1),
        CONSTRAINT CK_app_user_role CHECK (role_code IN ('customer', 'clerk', 'maker', 'stock', 'manager')),
        CONSTRAINT CK_app_user_binding CHECK (
            (role_code = 'customer' AND member_id IS NOT NULL AND employee_id IS NULL)
            OR (role_code <> 'customer' AND member_id IS NULL AND employee_id IS NOT NULL)
        )
    );
END;
GO

CREATE OR ALTER PROCEDURE dbo.usp_find_app_user @username NVARCHAR(30)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT u.username, u.password_hash, u.role_code, u.member_id, u.employee_id, u.display_name
    FROM dbo.app_user AS u
    LEFT JOIN dbo.staff_employee AS e ON e.employee_id = u.employee_id
    WHERE u.username = @username AND u.is_active = 1
      AND (u.role_code = 'customer' OR e.employ_status = 'ACTIVE');
END;
GO

GRANT EXECUTE ON dbo.usp_find_app_user TO role_manager;
GRANT VIEW DEFINITION ON dbo.app_user TO role_manager;
EXEC sys.sp_updateextendedproperty @name = N'milktea_schema_version', @value = N'2.2';
COMMIT TRANSACTION;
GO
