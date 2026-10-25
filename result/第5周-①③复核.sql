/* ============================================================================
   第5周-①③复核.sql —— 映射清单 / 问题清单的实物核对（只读）
   ----------------------------------------------------------------------------
   对象：v1.0 MilkTeaShop_v01（236 项）
   方式：从 sys.* 反向取事实，对照 docs/07-ER模型/业务规则与映射清单.md、
        v0.1-问题清单.md、sql/migration.sql、docs/02。
   输出：result/第5周-①③复核-20261025.txt
   ============================================================================ */
SET NOCOUNT ON;
USE [MilkTeaShop_v01];
GO

PRINT N'================================================================================';
PRINT N'1. 结构指纹（v1.0 应为 236）';
PRINT N'================================================================================';
SELECT
      (SELECT COUNT(*) FROM sys.tables) AS tables
    , (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) AS columns
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') AS pk
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ') AS uq_constraint
    , (SELECT COUNT(*) FROM sys.indexes i
        WHERE i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0
          AND i.is_hypothetical = 0
          AND i.object_id IN (SELECT object_id FROM sys.tables)) AS uq_filtered
    , (SELECT COUNT(*) FROM sys.foreign_keys) AS fk
    , (SELECT COUNT(*) FROM sys.check_constraints) AS ck
    , (SELECT COUNT(*) FROM sys.default_constraints) AS df;
GO

PRINT N'================================================================================';
PRINT N'2. P1–P8 / P12 约束是否存在（名称 + 定义）';
PRINT N'================================================================================';
SELECT t.name AS table_name, cc.name AS check_name, cc.definition
FROM sys.check_constraints cc
JOIN sys.tables t ON t.object_id = cc.parent_object_id
WHERE cc.name IN (
    N'CK_shop_order_order_mode_channel', N'CK_shop_order_pending',
    N'CK_inv_stock_log_order', N'CK_inv_stock_log_restock',
    N'CK_member_balance_log_order', N'CK_mkt_member_coupon_used',
    N'CK_item_product_unit_price', N'CK_order_item_unit_price',
    N'CK_shop_order_item_subtotal', N'CK_shop_order_coupon_discount',
    N'CK_shop_order_refund_amount', N'CK_order_platform_commission_rate',
    N'CK_order_platform_commission_amount', N'CK_order_item_addon_addon_price',
    N'CK_shop_order_total_amount_eq'
)
ORDER BY t.name, cc.name;
GO

PRINT N'================================================================================';
PRINT N'3. P7 筛选唯一索引';
PRINT N'================================================================================';
SELECT t.name AS table_name, i.name AS index_name, i.filter_definition
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
WHERE i.name = N'UQ_mkt_member_coupon_used_order';
GO

PRINT N'================================================================================';
PRINT N'4. 25 条外码（父表 → 子表）';
PRINT N'================================================================================';
SELECT fk.name AS fk_name, tp.name AS parent_table, tr.name AS referenced_table
FROM sys.foreign_keys fk
JOIN sys.tables tp ON tp.object_id = fk.parent_object_id
JOIN sys.tables tr ON tr.object_id = fk.referenced_object_id
ORDER BY tp.name, fk.name;
GO

PRINT N'================================================================================';
PRINT N'5. 可选外码的可空性（应全部为 1）';
PRINT N'================================================================================';
SELECT t.name AS table_name, c.name AS column_name, c.is_nullable
FROM sys.columns c
JOIN sys.tables t ON t.object_id = c.object_id
WHERE (t.name = 'shop_order' AND c.name = 'member_id')
   OR (t.name = 'inv_stock_log' AND c.name IN ('order_id', 'restock_id'))
   OR (t.name = 'mkt_member_coupon' AND c.name = 'used_order_id')
ORDER BY t.name, c.name;
GO

PRINT N'================================================================================';
PRINT N'6. 4 处 M:N 关联表 / 3 个弱实体 / 3 类审计流水';
PRINT N'================================================================================';
SELECT name, 'M:N 关联表' AS category FROM sys.tables
 WHERE name IN ('item_recipe', 'item_addon_option', 'order_item_addon', 'mkt_member_coupon')
UNION ALL
SELECT name, '弱实体' FROM sys.tables
 WHERE name IN ('order_item', 'order_platform', 'inv_restock_item')
UNION ALL
SELECT name, '审计流水' FROM sys.tables
 WHERE name IN ('inv_stock_log', 'member_point_log', 'member_balance_log')
ORDER BY category, name;
GO

PRINT N'================================================================================';
PRINT N'7. P1–P6 / P12 现有数据违规数（应全为 0）';
PRINT N'================================================================================';
SELECT 'P1_order_mode' AS check_name, COUNT(*) AS violations FROM dbo.shop_order
 WHERE NOT ((channel = 'PLATFORM' AND order_mode IS NULL)
         OR (channel = 'COUNTER'  AND order_mode IS NOT NULL))
UNION ALL
SELECT 'P2_pending_pay', COUNT(*) FROM dbo.shop_order
 WHERE NOT (order_status <> 'PENDING' OR pay_method IS NULL)
UNION ALL
SELECT 'P3_log_order', COUNT(*) FROM dbo.inv_stock_log
 WHERE NOT ((log_type = 'SALE' AND order_id IS NOT NULL)
         OR (log_type <> 'SALE' AND order_id IS NULL))
UNION ALL
SELECT 'P4_log_restock', COUNT(*) FROM dbo.inv_stock_log
 WHERE NOT ((log_type = 'IN' AND restock_id IS NOT NULL)
         OR (log_type <> 'IN' AND restock_id IS NULL))
UNION ALL
SELECT 'P5_balance_order', COUNT(*) FROM dbo.member_balance_log
 WHERE NOT ((balance_type = 'RECHARGE' AND order_id IS NULL)
         OR (balance_type <> 'RECHARGE' AND order_id IS NOT NULL))
UNION ALL
SELECT 'P6_coupon_used', COUNT(*) FROM dbo.mkt_member_coupon
 WHERE NOT ((coupon_status = 'UNUSED' AND used_order_id IS NULL)
         OR (coupon_status <> 'UNUSED' AND used_order_id IS NOT NULL))
UNION ALL
SELECT 'P7_dup_used_order', COUNT(*) FROM (
    SELECT used_order_id FROM dbo.mkt_member_coupon
     WHERE used_order_id IS NOT NULL GROUP BY used_order_id HAVING COUNT(*) > 1
) AS dup
UNION ALL
SELECT 'P12_total_eq', COUNT(*) FROM dbo.shop_order
 WHERE total_amount <> item_subtotal - coupon_discount;
GO

PRINT N'================================================================================';
PRINT N'8. P8 值约束违规数（应全为 0）';
PRINT N'================================================================================';
SELECT 'P8_item_product_price' AS check_name, COUNT(*) AS violations FROM dbo.item_product WHERE unit_price <= 0
UNION ALL SELECT 'P8_order_item_price', COUNT(*) FROM dbo.order_item WHERE unit_price <= 0
UNION ALL SELECT 'P8_subtotal_discount', COUNT(*) FROM dbo.shop_order WHERE item_subtotal < 0 OR coupon_discount < 0
UNION ALL SELECT 'P8_refund_amount', COUNT(*) FROM dbo.shop_order WHERE refund_amount < 0
UNION ALL SELECT 'P8_commission_rate', COUNT(*) FROM dbo.order_platform WHERE commission_rate < 0 OR commission_rate > 1
UNION ALL SELECT 'P8_commission_amount', COUNT(*) FROM dbo.order_platform WHERE commission_amount < 0
UNION ALL SELECT 'P8_addon_price', COUNT(*) FROM dbo.order_item_addon WHERE addon_price <= 0;
GO

PRINT N'================================================================================';
PRINT N'9. P9 未落地（item_addon_option 只有 3 条列级 CHECK，无 material_kind 跨表约束）';
PRINT N'================================================================================';
SELECT cc.name AS check_name, cc.definition
FROM sys.check_constraints cc
WHERE cc.parent_object_id = OBJECT_ID('dbo.item_addon_option')
ORDER BY cc.name;
GO

PRINT N'================================================================================';
PRINT N'10. R20 订单端 max=1 的结构证据（非空 used_order_id 每单最多一行）';
PRINT N'================================================================================';
SELECT used_order_id, COUNT(*) AS coupon_count
FROM dbo.mkt_member_coupon
WHERE used_order_id IS NOT NULL
GROUP BY used_order_id
ORDER BY used_order_id;
GO
