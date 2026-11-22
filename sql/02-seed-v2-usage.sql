:ON ERROR EXIT
SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF OBJECT_ID('dbo.order_material_usage', 'U') IS NULL
    THROW 51035, N'请先执行 v2.1 耗用快照迁移。', 1;

DECLARE @order_id CHAR(12) = '202609160005';
IF NOT EXISTS (
    SELECT 1 FROM dbo.shop_order WITH (UPDLOCK, HOLDLOCK)
    WHERE order_id = @order_id AND created_at = '2026-09-16T14:00:00'
      AND member_id = 'C0000002' AND order_status = 'PENDING'
)
    THROW 51036, N'找不到初始种子脚本中的待支付示例单。', 1;

IF (SELECT COUNT(*) FROM dbo.order_item WHERE order_id = @order_id) <> 1
   OR NOT EXISTS (
       SELECT 1 FROM dbo.order_item
       WHERE order_id = @order_id AND item_line = 1 AND product_id = 'P0000004' AND qty = 1
   )
   OR (SELECT COUNT(*) FROM dbo.order_item_addon WHERE order_id = @order_id) <> 1
   OR NOT EXISTS (
       SELECT 1 FROM dbo.order_item_addon
       WHERE order_id = @order_id AND item_line = 1 AND material_id = 'M0000001' AND qty = 1
   )
    THROW 51037, N'示例单明细和种子脚本不一致。', 1;

-- 固定取自 02-seed.sql 的单5：一杯椰果奶茶，另加一份珍珠。
DECLARE @expected TABLE (material_id CHAR(8) PRIMARY KEY, qty DECIMAL(10,2) NOT NULL);
INSERT @expected VALUES ('M0000001',30), ('M0000002',150), ('M0000003',5),
                        ('M0000004',25), ('M0000006',20), ('M0000007',1);

IF EXISTS (SELECT 1 FROM dbo.order_material_usage WHERE order_id = @order_id)
BEGIN
    IF EXISTS (
        SELECT material_id, qty FROM @expected
        EXCEPT SELECT material_id, qty FROM dbo.order_material_usage WHERE order_id = @order_id
    ) OR EXISTS (
        SELECT material_id, qty FROM dbo.order_material_usage WHERE order_id = @order_id
        EXCEPT SELECT material_id, qty FROM @expected
    )
        THROW 51038, N'已有耗用快照与示例不一致，需人工复核。', 1;
END
ELSE
BEGIN
    IF EXISTS (
        SELECT 1 FROM @expected AS e
        LEFT JOIN dbo.item_material AS m ON m.material_id = e.material_id
        WHERE m.material_id IS NULL OR m.reserved_qty < e.qty
    )
        THROW 51039, N'当前库存预占不足以覆盖示例单，需人工复核。', 1;

    INSERT dbo.order_material_usage (order_id, material_id, qty)
    SELECT @order_id, material_id, qty FROM @expected;
END;

COMMIT TRANSACTION;
GO
