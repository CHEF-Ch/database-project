/* ============================================================================
   query.sql —— 第 4 周：多表连接查询 + 统计查询
   ----------------------------------------------------------------------------
   前置：00-create.sql → 01-schema.sql → constraint.sql → 02-seed.sql
   说明：本文件只查询，不修改业务数据；可重复执行。
   ============================================================================ */
SET NOCOUNT ON;
USE [MilkTeaShop];
GO

/* 01. 商品销售明细：商品 + 订单明细 + 订单，统计每款商品售出杯数和销售额 */
SELECT
    p.product_id,
    p.name AS product_name,
    p.size,
    SUM(oi.qty) AS sold_qty,
    SUM(oi.qty * oi.unit_price) AS gross_amount
FROM dbo.item_product AS p
JOIN dbo.order_item AS oi
    ON oi.product_id = p.product_id
JOIN dbo.shop_order AS o
    ON o.order_id = oi.order_id
WHERE o.order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED')
GROUP BY p.product_id, p.name, p.size
ORDER BY sold_qty DESC, p.product_id;
GO

/* 02. 会员订单查询：会员 + 订单 + 订单明细，查看会员购买过什么 */
SELECT
    m.member_id,
    m.name AS member_name,
    o.order_id,
    o.created_at,
    o.order_status,
    p.name AS product_name,
    oi.size,
    oi.qty,
    oi.unit_price
FROM dbo.member_member AS m
JOIN dbo.shop_order AS o
    ON o.member_id = m.member_id
JOIN dbo.order_item AS oi
    ON oi.order_id = o.order_id
JOIN dbo.item_product AS p
    ON p.product_id = oi.product_id
ORDER BY o.created_at DESC, o.order_id, oi.item_line;
GO

/* 03. 平台订单对账：订单 + 平台信息，计算门店平台结算款 */
SELECT
    o.order_id,
    o.created_at,
    op.platform_name,
    op.platform_order_no,
    o.total_amount AS item_amount,
    op.commission_rate,
    op.commission_amount,
    o.total_amount - op.commission_amount AS settlement_amount,
    op.delivery_fee
FROM dbo.shop_order AS o
JOIN dbo.order_platform AS op
    ON op.order_id = o.order_id
WHERE o.channel = 'PLATFORM'
ORDER BY o.created_at, o.order_id;
GO

/* 04. 原料库存预警：原料 + 当前库存，找低于安全库存的原料 */
SELECT
    material_id,
    name AS material_name,
    unit,
    stock_qty,
    reserved_qty,
    safety_stock,
    stock_qty - reserved_qty AS available_qty,
    safety_stock - (stock_qty - reserved_qty) AS shortage_qty
FROM dbo.item_material
WHERE stock_qty - reserved_qty < safety_stock
ORDER BY shortage_qty DESC, material_id;
GO

/* 05. 商品加料配置：商品 + 加料原料 */
SELECT
    p.product_id,
    p.name AS product_name,
    m.material_id,
    m.name AS addon_name,
    a.addon_price,
    a.addon_qty,
    a.addon_status
FROM dbo.item_addon_option AS a
JOIN dbo.item_product AS p
    ON p.product_id = a.product_id
JOIN dbo.item_material AS m
    ON m.material_id = a.material_id
ORDER BY p.product_id, m.material_id;
GO

/* 06. 热门加料：订单加料 + 原料，按实际使用份数统计 */
SELECT
    m.material_id,
    m.name AS addon_name,
    SUM(oia.qty) AS used_qty,
    COUNT(DISTINCT oia.order_id) AS order_count
FROM dbo.order_item_addon AS oia
JOIN dbo.item_material AS m
    ON m.material_id = oia.material_id
JOIN dbo.shop_order AS o
    ON o.order_id = oia.order_id
WHERE o.order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED')
GROUP BY m.material_id, m.name
ORDER BY used_qty DESC, m.material_id;
GO

/* 07. 每日订单统计：订单按日期汇总 */
SELECT
    CAST(o.created_at AS date) AS order_date,
    COUNT(*) AS order_count,
    SUM(CASE WHEN o.order_status IN ('PAID', 'MAKING', 'COMPLETED') THEN 1 ELSE 0 END) AS active_paid_orders,
    SUM(CASE WHEN o.order_status = 'REFUNDED' THEN 1 ELSE 0 END) AS refunded_orders,
    SUM(o.total_amount) AS order_amount,
    SUM(CASE WHEN o.order_status = 'REFUNDED' THEN ISNULL(o.refund_amount, 0) ELSE 0 END) AS refund_amount
FROM dbo.shop_order AS o
GROUP BY CAST(o.created_at AS date)
ORDER BY order_date;
GO

/* 08. 员工经办订单统计：员工 + 订单，统计经办单量 */
SELECT
    e.employee_id,
    e.name AS employee_name,
    e.job_title,
    COUNT(o.order_id) AS handled_order_count,
    SUM(CASE WHEN o.order_status IN ('PAID', 'MAKING', 'COMPLETED') THEN o.total_amount ELSE 0 END) AS handled_amount
FROM dbo.staff_employee AS e
LEFT JOIN dbo.shop_order AS o
    ON o.employee_id = e.employee_id
GROUP BY e.employee_id, e.name, e.job_title
ORDER BY handled_order_count DESC, e.employee_id;
GO

/* 09. 补货统计：补货单 + 补货明细 + 原料 */
SELECT
    r.restock_id,
    r.supplier_name,
    r.restock_status,
    r.created_at,
    r.received_at,
    m.material_id,
    m.name AS material_name,
    ri.qty,
    m.unit
FROM dbo.inv_restock AS r
JOIN dbo.inv_restock_item AS ri
    ON ri.restock_id = r.restock_id
JOIN dbo.item_material AS m
    ON m.material_id = ri.material_id
ORDER BY r.created_at DESC, r.restock_id, m.material_id;
GO

/* 10. 库存流水追踪：原料 + 流水 + 订单/补货来源 */
SELECT
    l.stock_log_id,
    l.created_at,
    m.name AS material_name,
    l.log_type,
    l.qty,
    l.order_id,
    l.restock_id,
    e.name AS employee_name
FROM dbo.inv_stock_log AS l
JOIN dbo.item_material AS m
    ON m.material_id = l.material_id
JOIN dbo.staff_employee AS e
    ON e.employee_id = l.employee_id
ORDER BY l.created_at, l.stock_log_id;
GO

/* 11. 会员积分/储值流水审计：分开统计两类一对多流水，避免互相笛卡尔放大 */
SELECT
    m.member_id,
    m.name AS member_name,
    m.points,
    pl.point_log_id AS log_id,
    pl.point_type AS change_type,
    pl.points AS change_value,
    pl.order_id,
    pl.created_at
FROM dbo.member_member AS m
JOIN dbo.member_point_log AS pl
    ON pl.member_id = m.member_id

UNION ALL

SELECT
    m.member_id,
    m.name AS member_name,
    m.points,
    bl.balance_log_id AS log_id,
    bl.balance_type AS change_type,
    bl.amount AS change_value,
    bl.order_id,
    bl.created_at
FROM dbo.member_member AS m
JOIN dbo.member_balance_log AS bl
    ON bl.member_id = m.member_id
ORDER BY member_id, log_id;
GO

/* 12. 临期预警查询：正向入库时间 + 保质期天数推算到期日。
       注意：该查询是提醒性质，不代表批次剩余量。 */
SELECT
    m.material_id,
    m.name AS material_name,
    m.shelf_life_days,
    l.created_at AS restock_in_at,
    DATEADD(day, m.shelf_life_days, l.created_at) AS estimated_expire_at,
    DATEDIFF(day, CAST(GETDATE() AS date),
             CAST(DATEADD(day, m.shelf_life_days, l.created_at) AS date)) AS remaining_days
FROM dbo.item_material AS m
JOIN dbo.inv_stock_log AS l
    ON l.material_id = m.material_id
WHERE l.log_type = 'IN'
  AND m.shelf_life_days IS NOT NULL
ORDER BY estimated_expire_at, m.material_id;
GO

/* 13. 订单金额构成：订单 + 明细 + 加料，按订单核对商品金额与加料金额 */
WITH AddonAmount AS (
    SELECT
        oia.order_id,
        oia.item_line,
        SUM(oia.qty * a.addon_price) AS addon_amount
    FROM dbo.order_item_addon AS oia
    JOIN dbo.order_item AS oi
      ON oi.order_id = oia.order_id
     AND oi.item_line = oia.item_line
    JOIN dbo.item_addon_option AS a
      ON a.product_id = oi.product_id
     AND a.material_id = oia.material_id
    GROUP BY oia.order_id, oia.item_line
)
SELECT
    o.order_id,
    o.created_at,
    o.item_subtotal,
    o.coupon_discount,
    o.total_amount,
    SUM(oi.qty * oi.unit_price) AS item_amount_from_lines,
    ISNULL(SUM(aa.addon_amount), 0) AS addon_amount
FROM dbo.shop_order AS o
JOIN dbo.order_item AS oi
    ON oi.order_id = o.order_id
LEFT JOIN AddonAmount AS aa
    ON aa.order_id = oi.order_id
   AND aa.item_line = oi.item_line
GROUP BY o.order_id, o.created_at, o.item_subtotal, o.coupon_discount, o.total_amount
ORDER BY o.created_at, o.order_id;
GO

/* 14. 热门商品：售出杯数不低于阈值的商品（HAVING 过滤聚合结果） */
SELECT
    p.product_id,
    p.name AS product_name,
    p.size,
    SUM(oi.qty) AS sold_qty
FROM dbo.item_product AS p
JOIN dbo.order_item AS oi
    ON oi.product_id = p.product_id
JOIN dbo.shop_order AS o
    ON o.order_id = oi.order_id
WHERE o.order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED')
GROUP BY p.product_id, p.name, p.size
HAVING SUM(oi.qty) >= 3
ORDER BY sold_qty DESC, p.product_id;
GO
