/* 人工录入单号和待验收明细后调用。库存、IN 流水和状态在同一事务变更。 */
CREATE OR ALTER PROCEDURE dbo.usp_receive_restock
    @restock_id CHAR(8),
    @employee_id CHAR(8),
    @received_at DATETIME2(0) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    -- Switch off implicit starts for this call; an active caller transaction stays active.
    SET IMPLICIT_TRANSACTIONS OFF;

    DECLARE @owns_transaction BIT = CASE WHEN @@TRANCOUNT = 0 THEN 1 ELSE 0 END;
    DECLARE @previous_employee SQL_VARIANT = SESSION_CONTEXT(N'employee_id');
    DECLARE @context_changed BIT = 0;
    DECLARE @status VARCHAR(20), @material_id CHAR(8), @qty DECIMAL(10,2);
    SET @received_at = COALESCE(@received_at, SYSDATETIME());

    BEGIN TRY
        IF @owns_transaction = 1 BEGIN TRANSACTION;

        SELECT @status = restock_status
        FROM dbo.inv_restock WITH (UPDLOCK, HOLDLOCK)
        WHERE restock_id = @restock_id;

        IF @status IS NULL OR @status <> 'PENDING'
        BEGIN
            IF @owns_transaction = 1 COMMIT TRANSACTION;
            SELECT 0 AS affected;
            RETURN;
        END;

        IF NOT EXISTS (SELECT 1 FROM dbo.staff_employee WHERE employee_id = @employee_id)
            THROW 51202, N'验收员工不存在。', 1;

        DECLARE @items TABLE (material_id CHAR(8) PRIMARY KEY, qty DECIMAL(10,2) NOT NULL);
        INSERT @items (material_id, qty)
        SELECT material_id, qty
        FROM dbo.inv_restock_item WITH (HOLDLOCK)
        WHERE restock_id = @restock_id;

        IF NOT EXISTS (SELECT 1 FROM @items) OR EXISTS (SELECT 1 FROM @items WHERE qty <= 0)
            THROW 51203, N'补货单必须至少有一项正数入库明细。', 1;

        EXEC sys.sp_set_session_context @key = N'employee_id', @value = @employee_id;
        SET @context_changed = 1;

        DECLARE restock_items CURSOR LOCAL FAST_FORWARD FOR
            SELECT material_id, qty FROM @items ORDER BY material_id;
        OPEN restock_items;
        FETCH NEXT FROM restock_items INTO @material_id, @qty;
        WHILE @@FETCH_STATUS = 0
        BEGIN
            UPDATE dbo.item_material SET stock_qty = stock_qty + @qty
            WHERE material_id = @material_id;
            IF @@ROWCOUNT <> 1
                THROW 51204, N'补货对应原料不存在。', 1;
            INSERT dbo.inv_stock_log (material_id, log_type, qty, restock_id, employee_id, created_at)
            VALUES (@material_id, 'IN', @qty, @restock_id, @employee_id, @received_at);
            FETCH NEXT FROM restock_items INTO @material_id, @qty;
        END;
        CLOSE restock_items;
        DEALLOCATE restock_items;

        UPDATE dbo.inv_restock
        SET restock_status = 'RECEIVED', received_at = @received_at
        WHERE restock_id = @restock_id;
        EXEC sys.sp_set_session_context @key = N'employee_id', @value = @previous_employee;
        SET @context_changed = 0;

        IF @owns_transaction = 1 COMMIT TRANSACTION;
        SELECT 1 AS affected;
    END TRY
    BEGIN CATCH
        IF @owns_transaction = 1 AND XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        IF @context_changed = 1
            EXEC sys.sp_set_session_context @key = N'employee_id', @value = @previous_employee;
        THROW;
    END CATCH;
END;
GO

GRANT EXECUTE ON dbo.usp_receive_restock TO role_stock_keeper;
GO
