/* 支付确认已负责扣料和 SALE 流水。此过程只负责完成，业务校验由 v2.3 触发器执行。 */
CREATE OR ALTER PROCEDURE dbo.usp_complete_order
    @order_id CHAR(12),
    @completed_at DATETIME2(0) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    -- SET within a procedure is scoped to this call. Avoid a nested BEGIN when
    -- the client's implicit mode is on, without touching any active transaction.
    SET IMPLICIT_TRANSACTIONS OFF;

    DECLARE @owns_transaction BIT = CASE WHEN @@TRANCOUNT = 0 THEN 1 ELSE 0 END;
    DECLARE @updated INT;

    BEGIN TRY
        IF @owns_transaction = 1 BEGIN TRANSACTION;

        -- 拒绝在完成规则缺失或停用的库中执行。
        IF NOT EXISTS (
            SELECT 1 FROM sys.triggers
            WHERE object_id = OBJECT_ID('dbo.trg_shop_order_complete', 'TR') AND is_disabled = 0
        )
            THROW 51201, N'订单完成规则未启用。', 1;

        UPDATE dbo.shop_order
        SET order_status = 'COMPLETED',
            completed_at = COALESCE(@completed_at, SYSDATETIME())
        WHERE order_id = @order_id AND order_status = 'MAKING';
        SET @updated = @@ROWCOUNT;

        IF @owns_transaction = 1 COMMIT TRANSACTION;
        SELECT @updated AS affected;
    END TRY
    BEGIN CATCH
        IF @owns_transaction = 1 AND XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

GRANT EXECUTE ON dbo.usp_complete_order TO role_app;
GO
