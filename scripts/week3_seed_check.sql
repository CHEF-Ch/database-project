/* ============================================================================
   第三周 步骤 ④ 样例数据装载 —— 复核检查（只读）
   ----------------------------------------------------------------------------
   对象：sql/02-seed.sql 装载后的运行库 MilkTeaShop（v0.1）
   复核人：Chen
   说明：本脚本只做 SELECT，不修改任何数据。
   运行：
       sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i scripts/week3_seed_check.sql
   ============================================================================ */
USE [MilkTeaShop];
GO

SET NOCOUNT ON;

PRINT '【1】17 张表行数';
SELECT t.name AS tbl, SUM(p.rows) AS rows
FROM sys.tables t
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY t.name
ORDER BY t.name;

PRINT '【2】订单覆盖（渠道 / 模式 / 状态 / 支付）';
SELECT channel,
       ISNULL(order_mode, '(NULL)') AS order_mode,
       order_status,
       ISNULL(pay_method, '(NULL)') AS pay_method,
       COUNT(*) AS cnt
FROM dbo.shop_order
GROUP BY channel, order_mode, order_status, pay_method
ORDER BY channel, order_mode, order_status, pay_method;

PRINT '【3】订单不变量（应为 7 / 0 / 0）';
SELECT COUNT(*) AS orders,
       SUM(CASE WHEN employee_id IS NULL THEN 1 ELSE 0 END) AS null_employee,
       SUM(CASE WHEN total_amount <> item_subtotal - coupon_discount THEN 1 ELSE 0 END) AS bad_amount
FROM dbo.shop_order;

PRINT '【4】会员快照与流水是否一致';
SELECT m.member_id, m.points, m.balance,
       (SELECT ISNULL(SUM(CASE WHEN p.point_type = 'REVERSAL' THEN -p.points ELSE p.points END), 0)
          FROM dbo.member_point_log p WHERE p.member_id = m.member_id) AS point_calc,
       (SELECT ISNULL(SUM(CASE WHEN b.balance_type = 'SPEND' THEN -b.amount ELSE b.amount END), 0)
          FROM dbo.member_balance_log b WHERE b.member_id = m.member_id) AS balance_calc
FROM dbo.member_member m
ORDER BY m.member_id;

PRINT '【5】券状态覆盖（应为 UNUSED / USED / RETURNED）';
SELECT coupon_status, COUNT(*) AS cnt
FROM dbo.mkt_member_coupon
GROUP BY coupon_status
ORDER BY coupon_status;

PRINT '【6】约束启用状态（应为 0 / 0）';
SELECT
    (SELECT COUNT(*) FROM sys.foreign_keys WHERE is_disabled = 1) AS disabled_fk,
    (SELECT COUNT(*) FROM sys.check_constraints WHERE is_disabled = 1) AS disabled_ck;
GO
