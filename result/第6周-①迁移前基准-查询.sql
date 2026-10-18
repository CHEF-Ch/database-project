/* ============================================================================
   第6周-①迁移前基准-查询.sql —— 迁移前基准（只读，可重复执行）
   ----------------------------------------------------------------------------
   三组基准：① 订单明细  ② 商品销售统计  ③ 库存查询
   另附：④ 基线摘要  ⑤ 口径核对（会员快照/待支付预占/推算期初/订单金额等式）
   输出：result/第6周-①迁移前基准-20261018.txt
   用途：第六周 ⑤ regression.sql 以本文件输出为"迁移前基准"，逐项对比迁移后结果。
   ============================================================================ */
SET NOCOUNT ON;
USE [MilkTeaShop];
GO

PRINT N'================================================================================';
PRINT N'【基准一】订单明细（订单头 + 明细行 + 该行加料金额）';
PRINT N'================================================================================';
WITH AddonAmount AS (
    SELECT oia.order_id, oia.item_line,
           SUM(oia.qty * a.addon_price) AS addon_amount
    FROM dbo.order_item_addon AS oia
    JOIN dbo.order_item AS oi
      ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
    LEFT JOIN dbo.item_addon_option AS a
      ON a.product_id = oi.product_id AND a.material_id = oia.material_id
    GROUP BY oia.order_id, oia.item_line
)
SELECT
      o.order_id
    , o.created_at
    , o.channel
    , o.order_mode
    , o.order_status
    , o.item_subtotal
    , o.coupon_discount
    , o.total_amount
    , oi.item_line
    , p.product_id
    , p.name AS product_name
    , oi.size
    , oi.sugar_level
    , oi.ice_level
    , oi.temp_level
    , oi.qty
    , oi.unit_price
    , oi.qty * oi.unit_price AS line_amount
    , ISNULL(aa.addon_amount, 0) AS addon_amount
FROM dbo.shop_order AS o
JOIN dbo.order_item AS oi
  ON oi.order_id = o.order_id
JOIN dbo.item_product AS p
  ON p.product_id = oi.product_id
LEFT JOIN AddonAmount AS aa
  ON aa.order_id = oi.order_id AND aa.item_line = oi.item_line
ORDER BY o.order_id, oi.item_line;
GO

PRINT N'';
PRINT N'【基准一·附】订单加料明细';
PRINT N'================================================================================';
SELECT
      oia.order_id
    , oia.item_line
    , oia.material_id
    , m.name AS material_name
    , oia.qty AS addon_qty
    , a.addon_price
    , oia.qty * a.addon_price AS addon_amount
FROM dbo.order_item_addon AS oia
JOIN dbo.item_material AS m
  ON m.material_id = oia.material_id
JOIN dbo.order_item AS oi
  ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
LEFT JOIN dbo.item_addon_option AS a
  ON a.product_id = oi.product_id AND a.material_id = oia.material_id
ORDER BY oia.order_id, oia.item_line, oia.material_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【基准二】商品销售统计（只计 PAID/MAKING/COMPLETED/REFUNDED，与 v_product_sales_summary 同口径）';
PRINT N'================================================================================';
SELECT
      p.product_id
    , p.name AS product_name
    , p.size
    , COUNT(DISTINCT oi.order_id) AS order_count
    , SUM(oi.qty) AS sold_qty
    , SUM(oi.qty * oi.unit_price) AS gross_amount
FROM dbo.item_product AS p
JOIN dbo.order_item AS oi
  ON oi.product_id = p.product_id
JOIN dbo.shop_order AS o
  ON o.order_id = oi.order_id
WHERE o.order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED')
GROUP BY p.product_id, p.name, p.size
ORDER BY sold_qty DESC, p.product_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【基准三】库存查询（全部原料 + 可售量与状态）';
PRINT N'================================================================================';
SELECT
      m.material_id
    , m.name AS material_name
    , m.unit
    , m.material_kind
    , m.stock_qty
    , m.reserved_qty
    , m.stock_qty - m.reserved_qty AS available_qty
    , m.safety_stock
    , CASE WHEN m.stock_qty - m.reserved_qty < m.safety_stock THEN 'LOW' ELSE 'OK' END AS stock_status
    , m.shelf_life_days
FROM dbo.item_material AS m
ORDER BY m.material_id;
GO

PRINT N'';
PRINT N'【基准三·附】低于安全库存的原料（query.sql 04 同口径）';
PRINT N'================================================================================';
SELECT
      m.material_id
    , m.name AS material_name
    , m.unit
    , m.stock_qty
    , m.reserved_qty
    , m.safety_stock
    , m.stock_qty - m.reserved_qty AS available_qty
    , m.safety_stock - (m.stock_qty - m.reserved_qty) AS shortage_qty
FROM dbo.item_material AS m
WHERE m.stock_qty - m.reserved_qty < m.safety_stock
ORDER BY shortage_qty DESC, m.material_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【基线摘要】表行数与关键金额';
PRINT N'================================================================================';
SELECT t.name AS table_name,
       SUM(p.rows) AS row_count
FROM sys.tables t
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1)
GROUP BY t.name
ORDER BY t.name;

SELECT
      COUNT(*) AS order_count
    , SUM(item_subtotal) AS sum_item_subtotal
    , SUM(coupon_discount) AS sum_coupon_discount
    , SUM(total_amount) AS sum_total_amount
    , SUM(ISNULL(refund_amount, 0)) AS sum_refund_amount
    , SUM(CASE WHEN order_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_orders
    , SUM(CASE WHEN order_status = 'REFUNDED' THEN 1 ELSE 0 END) AS refunded_orders
FROM dbo.shop_order;

SELECT member_id, points, balance FROM dbo.member_member ORDER BY member_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【口径核对 5.1】会员积分/余额快照 vs 流水净额（差异应为 0）';
PRINT N'================================================================================';
WITH PointNet AS (
    SELECT member_id,
           SUM(CASE WHEN point_type = 'REVERSAL' THEN -points ELSE points END) AS net_points
    FROM dbo.member_point_log
    GROUP BY member_id
), BalanceNet AS (
    SELECT member_id,
           SUM(CASE balance_type WHEN 'RECHARGE' THEN amount
                                 WHEN 'REFUND'   THEN amount
                                 ELSE -amount END) AS net_amount
    FROM dbo.member_balance_log
    GROUP BY member_id
)
SELECT
      m.member_id
    , m.points
    , ISNULL(pn.net_points, 0) AS log_net_points
    , m.points - ISNULL(pn.net_points, 0) AS points_diff
    , m.balance
    , ISNULL(bn.net_amount, 0) AS log_net_amount
    , m.balance - ISNULL(bn.net_amount, 0) AS balance_diff
FROM dbo.member_member AS m
LEFT JOIN PointNet AS pn   ON pn.member_id = m.member_id
LEFT JOIN BalanceNet AS bn ON bn.member_id = m.member_id
ORDER BY m.member_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【口径核对 5.2】待支付（PENDING）订单的原料需求 vs reserved_qty（差异应为 0）';
PRINT N'================================================================================';
WITH PendingRecipe AS (
    SELECT r.material_id, SUM(r.qty * oi.qty) AS req_qty
    FROM dbo.shop_order AS o
    JOIN dbo.order_item AS oi ON oi.order_id = o.order_id
    JOIN dbo.item_recipe AS r ON r.product_id = oi.product_id
    WHERE o.order_status = 'PENDING'
    GROUP BY r.material_id
), PendingAddon AS (
    SELECT a.material_id, SUM(a.addon_qty * oia.qty) AS req_qty
    FROM dbo.shop_order AS o
    JOIN dbo.order_item_addon AS oia ON oia.order_id = o.order_id
    JOIN dbo.order_item AS oi
      ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
    JOIN dbo.item_addon_option AS a
      ON a.product_id = oi.product_id AND a.material_id = oia.material_id
    WHERE o.order_status = 'PENDING'
    GROUP BY a.material_id
), PendingReq AS (
    SELECT material_id, SUM(req_qty) AS req_qty
    FROM (SELECT material_id, req_qty FROM PendingRecipe
          UNION ALL
          SELECT material_id, req_qty FROM PendingAddon) AS x
    GROUP BY material_id
)
SELECT
      m.material_id
    , m.name AS material_name
    , m.reserved_qty
    , ISNULL(pr.req_qty, 0) AS pending_req_qty
    , m.reserved_qty - ISNULL(pr.req_qty, 0) AS diff
FROM dbo.item_material AS m
LEFT JOIN PendingReq AS pr ON pr.material_id = m.material_id
ORDER BY m.material_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【口径核对 5.3】库存快照 vs 流水净变动（推算期初 = 快照 − 流水净变动）';
PRINT N'说明：期初库存未写流水，故"快照 = Σ流水"不成立；本表把推算期初显式记下来，';
PRINT N'      迁移前后应保持不变，回归对比看"快照、流水净变动、推算期初"三列即可。';
PRINT N'================================================================================';
WITH LogNet AS (
    SELECT material_id,
           SUM(CASE log_type WHEN 'IN'    THEN qty
                             WHEN 'GAIN'  THEN qty
                             WHEN 'SALE'  THEN -qty
                             WHEN 'LOSS'  THEN -qty
                             WHEN 'SHORT' THEN -qty END) AS net_change
    FROM dbo.inv_stock_log
    GROUP BY material_id
)
SELECT
      m.material_id
    , m.name AS material_name
    , m.stock_qty
    , ISNULL(l.net_change, 0) AS log_net_change
    , m.stock_qty - ISNULL(l.net_change, 0) AS implied_opening_qty
    , (SELECT COUNT(*) FROM dbo.inv_stock_log AS x WHERE x.material_id = m.material_id) AS log_rows
FROM dbo.item_material AS m
LEFT JOIN LogNet AS l ON l.material_id = m.material_id
ORDER BY m.material_id;
GO

PRINT N'';
PRINT N'================================================================================';
PRINT N'【口径核对 5.4】订单金额等式（total = subtotal − discount；subtotal = 行金额 + 加料金额）';
PRINT N'================================================================================';
WITH AddonAmount AS (
    SELECT oia.order_id, oia.item_line,
           SUM(oia.qty * a.addon_price) AS addon_amount
    FROM dbo.order_item_addon AS oia
    JOIN dbo.order_item AS oi
      ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
    LEFT JOIN dbo.item_addon_option AS a
      ON a.product_id = oi.product_id AND a.material_id = oia.material_id
    GROUP BY oia.order_id, oia.item_line
), LineAmount AS (
    SELECT order_id, SUM(qty * unit_price) AS line_amount
    FROM dbo.order_item
    GROUP BY order_id
), AddonTotal AS (
    SELECT order_id, SUM(addon_amount) AS addon_amount
    FROM AddonAmount
    GROUP BY order_id
)
SELECT
      o.order_id
    , o.item_subtotal
    , o.coupon_discount
    , o.total_amount
    , o.item_subtotal - o.coupon_discount AS calc_total
    , o.total_amount - (o.item_subtotal - o.coupon_discount) AS total_diff
    , ISNULL(la.line_amount, 0) AS line_amount
    , ISNULL(at.addon_amount, 0) AS addon_amount
    , ISNULL(la.line_amount, 0) + ISNULL(at.addon_amount, 0) - o.item_subtotal AS subtotal_diff
FROM dbo.shop_order AS o
LEFT JOIN LineAmount AS la ON la.order_id = o.order_id
LEFT JOIN AddonTotal AS at ON at.order_id = o.order_id
ORDER BY o.order_id;
GO
