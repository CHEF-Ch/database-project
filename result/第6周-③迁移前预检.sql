/* ============================================================================
   第6周-③迁移前预检.sql —— 迁移前数据兼容性检查（只读）
   ----------------------------------------------------------------------------
   对象：迁移副本 MilkTeaShop_v01（v0.1）
   目的：在写 migration.sql 之前，确认现有数据满足 P1–P8、P12 与 S1 回填前提。
        所有 violations 必须为 0；任一非 0 都要先修数据或调整约束。
   输出：result/第6周-③迁移前预检-20261018.txt
   ============================================================================ */
SET NOCOUNT ON;
USE [MilkTeaShop_v01];
GO

SELECT 'P1_order_mode'        AS check_name, COUNT(*) AS violations FROM dbo.shop_order
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
     WHERE used_order_id IS NOT NULL
     GROUP BY used_order_id HAVING COUNT(*) > 1
) AS dup
UNION ALL
SELECT 'P8_item_product_price', COUNT(*) FROM dbo.item_product WHERE unit_price <= 0
UNION ALL
SELECT 'P8_order_item_price', COUNT(*) FROM dbo.order_item WHERE unit_price <= 0
UNION ALL
SELECT 'P8_subtotal_discount', COUNT(*) FROM dbo.shop_order WHERE item_subtotal < 0 OR coupon_discount < 0
UNION ALL
SELECT 'P8_refund_amount', COUNT(*) FROM dbo.shop_order WHERE refund_amount < 0
UNION ALL
SELECT 'P8_commission_rate', COUNT(*) FROM dbo.order_platform WHERE commission_rate < 0 OR commission_rate > 1
UNION ALL
SELECT 'P8_commission_amount', COUNT(*) FROM dbo.order_platform WHERE commission_amount < 0
UNION ALL
SELECT 'P12_total_eq', COUNT(*) FROM dbo.shop_order WHERE total_amount <> item_subtotal - coupon_discount
UNION ALL
SELECT 'S1_unmatched_addon', COUNT(*) FROM dbo.order_item_addon AS oia
 LEFT JOIN dbo.order_item AS oi
   ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
 LEFT JOIN dbo.item_addon_option AS a
   ON a.product_id = oi.product_id AND a.material_id = oia.material_id
 WHERE a.addon_price IS NULL;
GO

/* 行数基线（迁移后应保持不变） */
SELECT t.name AS table_name, SUM(p.rows) AS row_count
FROM sys.tables t
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
GROUP BY t.name
ORDER BY t.name;
GO
