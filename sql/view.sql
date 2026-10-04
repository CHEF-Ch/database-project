/* ============================================================================
   view.sql —— 第 4 周：统计视图
   ----------------------------------------------------------------------------
   前置：00-create.sql → 01-schema.sql → constraint.sql → 02-seed.sql
   说明：使用 CREATE OR ALTER，可重复执行；只创建查询视图，不修改业务数据。
   ============================================================================ */
SET NOCOUNT ON;
USE [MilkTeaShop];
GO

/* 视图 1：商品销售汇总
   用途：统计商品售出杯数、订单数、销售金额。 */
CREATE OR ALTER VIEW dbo.v_product_sales_summary
AS
SELECT
    p.product_id,
    p.name AS product_name,
    p.size,
    COUNT(DISTINCT oi.order_id) AS order_count,
    SUM(oi.qty) AS sold_qty,
    SUM(oi.qty * oi.unit_price) AS gross_amount
FROM dbo.item_product AS p
JOIN dbo.order_item AS oi
    ON oi.product_id = p.product_id
JOIN dbo.shop_order AS o
    ON o.order_id = oi.order_id
WHERE o.order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED')
GROUP BY p.product_id, p.name, p.size;
GO

/* 视图 2：每日经营统计
   用途：按订单日期查看订单量、成交金额和退款金额。 */
CREATE OR ALTER VIEW dbo.v_daily_order_summary
AS
SELECT
    CAST(o.created_at AS date) AS order_date,
    COUNT(*) AS order_count,
    SUM(CASE WHEN o.order_status IN ('PAID', 'MAKING', 'COMPLETED') THEN 1 ELSE 0 END) AS paid_order_count,
    SUM(CASE WHEN o.order_status = 'REFUNDED' THEN 1 ELSE 0 END) AS refunded_order_count,
    SUM(o.total_amount) AS order_amount,
    SUM(CASE WHEN o.order_status = 'REFUNDED' THEN ISNULL(o.refund_amount, 0) ELSE 0 END) AS refund_amount,
    SUM(o.total_amount) - SUM(CASE WHEN o.order_status = 'REFUNDED' THEN ISNULL(o.refund_amount, 0) ELSE 0 END) AS net_amount
FROM dbo.shop_order AS o
GROUP BY CAST(o.created_at AS date);
GO

/* 视图 3：原料临期预警
   用途：根据入库流水时间 + 原料保质期天数推算到期日。
   注意：项目文档明确说明这是提醒性质，不追踪批次剩余量。 */
CREATE OR ALTER VIEW dbo.v_material_expiry_alert
AS
SELECT
    m.material_id,
    m.name AS material_name,
    m.unit,
    m.shelf_life_days,
    l.stock_log_id,
    l.created_at AS restock_in_at,
    DATEADD(day, m.shelf_life_days, l.created_at) AS estimated_expire_at,
    DATEDIFF(day, CAST(GETDATE() AS date),
             CAST(DATEADD(day, m.shelf_life_days, l.created_at) AS date)) AS remaining_days,
    CASE
        WHEN DATEADD(day, m.shelf_life_days, l.created_at) < CAST(GETDATE() AS date) THEN 'EXPIRED'
        WHEN DATEDIFF(day, CAST(GETDATE() AS date),
                      CAST(DATEADD(day, m.shelf_life_days, l.created_at) AS date)) <= 7 THEN 'EXPIRING'
        ELSE 'NORMAL'
    END AS expiry_status
FROM dbo.item_material AS m
JOIN dbo.inv_stock_log AS l
    ON l.material_id = m.material_id
WHERE l.log_type = 'IN'
  AND m.shelf_life_days IS NOT NULL;
GO

/* 视图 4：会员消费汇总
   用途：会员 + 订单统计，便于查看会员消费次数和金额。 */
CREATE OR ALTER VIEW dbo.v_member_order_summary
AS
SELECT
    m.member_id,
    m.name AS member_name,
    COUNT(o.order_id) AS order_count,
    SUM(CASE WHEN o.order_status IN ('PAID', 'MAKING', 'COMPLETED') THEN 1 ELSE 0 END) AS paid_order_count,
    SUM(CASE WHEN o.order_status IN ('PAID', 'MAKING', 'COMPLETED') THEN o.total_amount ELSE 0 END) AS paid_amount,
    MAX(o.created_at) AS last_order_at
FROM dbo.member_member AS m
LEFT JOIN dbo.shop_order AS o
    ON o.member_id = m.member_id
GROUP BY m.member_id, m.name;
GO

/* 验证：查询视图定义是否成功创建 */
SELECT name, type_desc
FROM sys.views
WHERE name IN (
    'v_product_sales_summary',
    'v_daily_order_summary',
    'v_material_expiry_alert',
    'v_member_order_summary'
)
ORDER BY name;
GO
