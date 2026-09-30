/*
===============================================================================
crud.sql —— 奶茶店数据库 CRUD 演示
===============================================================================
适用：SQL Server / T-SQL
前置：sql/00-create.sql -> sql/01-schema.sql -> sql/constraint.sql
      -> sql/02-seed.sql

设计原则：
1. 所有“写操作”均放在事务中，演示结束 ROLLBACK，不污染 02-seed.sql。
2. 查询（Read）使用 JOIN 展示业务上真正有意义的数据，而不是只 SELECT *。
3. 订单、库存、会员资产存在业务依赖，因此给出“单表 CRUD”和“业务组合 CRUD”。
4. 库存流水、积分流水、储值流水属于审计流水：只允许 INSERT / SELECT，
   不建议 UPDATE / DELETE 修改历史记录。
5. 脚本自身 SET / USE 上下文完整，可直接用 SSMS 或 sqlcmd 执行。
===============================================================================
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

USE [MilkTeaShop];
GO

/* ============================================================================
   一、商品 CRUD：item_product
   ============================================================================ */

PRINT N'========== 1. item_product 商品 CRUD ==========';

-- R：查询商品目录
SELECT
    product_id,
    name,
    size,
    unit_price,
    sale_status
FROM dbo.item_product
ORDER BY product_id;

-- C/U/D：新增、修改、删除测试商品
BEGIN TRANSACTION;

INSERT INTO dbo.item_product
    (product_id, name, size, unit_price, sale_status)
VALUES
    ('P9999001', N'CRUD测试奶茶', 'MEDIUM', 13.50, 'ON_SALE');

SELECT *
FROM dbo.item_product
WHERE product_id = 'P9999001';

UPDATE dbo.item_product
SET unit_price = 14.00
WHERE product_id = 'P9999001';

SELECT *
FROM dbo.item_product
WHERE product_id = 'P9999001';

DELETE FROM dbo.item_product
WHERE product_id = 'P9999001';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   二、原料 CRUD：item_material
   ============================================================================ */

PRINT N'========== 2. item_material 原料 CRUD ==========';

SELECT
    material_id,
    name,
    unit,
    material_kind,
    stock_qty,
    reserved_qty,
    stock_qty - reserved_qty AS available_qty,
    safety_stock,
    shelf_life_days
FROM dbo.item_material
ORDER BY material_id;

BEGIN TRANSACTION;

INSERT INTO dbo.item_material
    (material_id, name, unit, material_kind,
     stock_qty, reserved_qty, safety_stock, shelf_life_days)
VALUES
    ('M9999001', N'CRUD测试原料', N'克', 'INGREDIENT',
     1000, 0, 200, 30);

SELECT *
FROM dbo.item_material
WHERE material_id = 'M9999001';

UPDATE dbo.item_material
SET safety_stock = 300
WHERE material_id = 'M9999001';

SELECT
    material_id,
    name,
    stock_qty,
    reserved_qty,
    stock_qty - reserved_qty AS available_qty,
    safety_stock
FROM dbo.item_material
WHERE material_id = 'M9999001';

DELETE FROM dbo.item_material
WHERE material_id = 'M9999001';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   三、会员 CRUD：member_member
   ============================================================================ */

PRINT N'========== 3. member_member 会员 CRUD ==========';

SELECT
    member_id,
    phone,
    name,
    points,
    balance,
    created_at
FROM dbo.member_member
ORDER BY member_id;

BEGIN TRANSACTION;

INSERT INTO dbo.member_member
    (member_id, phone, name, points, balance, created_at)
VALUES
    ('C9999001', '13999990001', N'CRUD测试会员',
     0, 0.00, SYSDATETIME());

SELECT *
FROM dbo.member_member
WHERE member_id = 'C9999001';

UPDATE dbo.member_member
SET
    name = N'CRUD测试会员-已修改',
    phone = '13999990002'
WHERE member_id = 'C9999001';

SELECT *
FROM dbo.member_member
WHERE member_id = 'C9999001';

DELETE FROM dbo.member_member
WHERE member_id = 'C9999001';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   四、员工 CRUD：staff_employee
   ============================================================================ */

PRINT N'========== 4. staff_employee 员工 CRUD ==========';

SELECT
    employee_id,
    name,
    job_title,
    employ_status
FROM dbo.staff_employee
ORDER BY employee_id;

BEGIN TRANSACTION;

INSERT INTO dbo.staff_employee
    (employee_id, name, job_title, employ_status)
VALUES
    ('E9999001', N'CRUD测试员工', 'CASHIER', 'ACTIVE');

UPDATE dbo.staff_employee
SET employ_status = 'RESIGNED'
WHERE employee_id = 'E9999001';

SELECT *
FROM dbo.staff_employee
WHERE employee_id = 'E9999001';

DELETE FROM dbo.staff_employee
WHERE employee_id = 'E9999001';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   五、配方 CRUD：item_recipe
   ============================================================================ */

PRINT N'========== 5. item_recipe 配方 CRUD ==========';

SELECT
    r.product_id,
    p.name AS product_name,
    p.size,
    r.material_id,
    m.name AS material_name,
    m.unit,
    r.qty
FROM dbo.item_recipe AS r
JOIN dbo.item_product AS p
    ON p.product_id = r.product_id
JOIN dbo.item_material AS m
    ON m.material_id = r.material_id
WHERE r.product_id = 'P0000003'
ORDER BY r.material_id;

BEGIN TRANSACTION;

INSERT INTO dbo.item_recipe
    (product_id, material_id, qty)
VALUES
    ('P0000003', 'M0000005', 30);

UPDATE dbo.item_recipe
SET qty = 35
WHERE product_id = 'P0000003'
  AND material_id = 'M0000005';

SELECT
    r.product_id,
    p.name AS product_name,
    m.name AS material_name,
    r.qty
FROM dbo.item_recipe AS r
JOIN dbo.item_product AS p
    ON p.product_id = r.product_id
JOIN dbo.item_material AS m
    ON m.material_id = r.material_id
WHERE r.product_id = 'P0000003'
  AND r.material_id = 'M0000005';

DELETE FROM dbo.item_recipe
WHERE product_id = 'P0000003'
  AND material_id = 'M0000005';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   六、加料选项 CRUD：item_addon_option
   ============================================================================ */

PRINT N'========== 6. item_addon_option 加料选项 CRUD ==========';

SELECT
    a.product_id,
    p.name AS product_name,
    a.material_id,
    m.name AS addon_name,
    a.addon_price,
    a.addon_qty,
    a.addon_status
FROM dbo.item_addon_option AS a
JOIN dbo.item_product AS p
    ON p.product_id = a.product_id
JOIN dbo.item_material AS m
    ON m.material_id = a.material_id
WHERE a.product_id = 'P0000003'
ORDER BY a.material_id;

BEGIN TRANSACTION;

INSERT INTO dbo.item_addon_option
    (product_id, material_id, addon_price, addon_qty, addon_status)
VALUES
    ('P0000003', 'M0000007', 1.00, 1, 'AVAILABLE');

UPDATE dbo.item_addon_option
SET addon_status = 'UNAVAILABLE'
WHERE product_id = 'P0000003'
  AND material_id = 'M0000007';

SELECT *
FROM dbo.item_addon_option
WHERE product_id = 'P0000003'
  AND material_id = 'M0000007';

DELETE FROM dbo.item_addon_option
WHERE product_id = 'P0000003'
  AND material_id = 'M0000007';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   七、优惠券规则 CRUD：mkt_coupon_rule
   ============================================================================ */

PRINT N'========== 7. mkt_coupon_rule 优惠券规则 CRUD ==========';

SELECT
    coupon_rule_id,
    coupon_name,
    coupon_type,
    threshold_amount,
    discount_amount,
    valid_from,
    valid_to
FROM dbo.mkt_coupon_rule
ORDER BY coupon_rule_id;

BEGIN TRANSACTION;

INSERT INTO dbo.mkt_coupon_rule
    (coupon_rule_id, coupon_name, coupon_type,
     threshold_amount, discount_amount, valid_from, valid_to)
VALUES
    ('CP999901', N'CRUD测试满50减8', 'FULL_REDUCTION',
     50.00, 8.00, '2026-09-01', '2026-12-31');

UPDATE dbo.mkt_coupon_rule
SET valid_to = '2027-01-31'
WHERE coupon_rule_id = 'CP999901';

SELECT *
FROM dbo.mkt_coupon_rule
WHERE coupon_rule_id = 'CP999901';

DELETE FROM dbo.mkt_coupon_rule
WHERE coupon_rule_id = 'CP999901';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   八、会员持券 CRUD：mkt_member_coupon
   ============================================================================ */

PRINT N'========== 8. mkt_member_coupon 会员持券 CRUD ==========';

SELECT
    mc.member_coupon_id,
    mc.member_id,
    mm.name AS member_name,
    mc.coupon_rule_id,
    cr.coupon_name,
    cr.threshold_amount,
    cr.discount_amount,
    mc.coupon_status,
    mc.used_order_id,
    mc.issued_at
FROM dbo.mkt_member_coupon AS mc
JOIN dbo.member_member AS mm
    ON mm.member_id = mc.member_id
JOIN dbo.mkt_coupon_rule AS cr
    ON cr.coupon_rule_id = mc.coupon_rule_id
WHERE mc.member_id = 'C0000001'
ORDER BY mc.issued_at DESC;

BEGIN TRANSACTION;

INSERT INTO dbo.mkt_member_coupon
    (coupon_rule_id, member_id, coupon_status, used_order_id, issued_at)
VALUES
    ('CP000001', 'C0000002', 'UNUSED', NULL, SYSDATETIME());

DECLARE @DemoMemberCouponId BIGINT = CONVERT(BIGINT, SCOPE_IDENTITY());

UPDATE dbo.mkt_member_coupon
SET coupon_status = 'LOCKED'
WHERE member_coupon_id = @DemoMemberCouponId;

SELECT *
FROM dbo.mkt_member_coupon
WHERE member_coupon_id = @DemoMemberCouponId;

UPDATE dbo.mkt_member_coupon
SET
    coupon_status = 'USED',
    used_order_id = '202609160005'
WHERE member_coupon_id = @DemoMemberCouponId;

SELECT *
FROM dbo.mkt_member_coupon
WHERE member_coupon_id = @DemoMemberCouponId;

DELETE FROM dbo.mkt_member_coupon
WHERE member_coupon_id = @DemoMemberCouponId;

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   九、补货 CRUD：inv_restock + inv_restock_item
   ============================================================================ */

PRINT N'========== 9. inv_restock 补货 CRUD ==========';

SELECT
    r.restock_id,
    r.supplier_name,
    r.restock_status,
    e.name AS employee_name,
    r.created_at,
    r.received_at
FROM dbo.inv_restock AS r
JOIN dbo.staff_employee AS e
    ON e.employee_id = r.employee_id
ORDER BY r.created_at DESC;

BEGIN TRANSACTION;

INSERT INTO dbo.inv_restock
    (restock_id, supplier_name, restock_status,
     employee_id, created_at, received_at)
VALUES
    ('R9999001', N'CRUD测试供应商', 'PENDING',
     'E0000003', SYSDATETIME(), NULL);

INSERT INTO dbo.inv_restock_item
    (restock_id, material_id, qty)
VALUES
    ('R9999001', 'M0000002', 500);

UPDATE dbo.inv_restock
SET
    restock_status = 'RECEIVED',
    received_at = SYSDATETIME()
WHERE restock_id = 'R9999001';

SELECT
    r.restock_id,
    r.supplier_name,
    r.restock_status,
    ri.material_id,
    m.name AS material_name,
    ri.qty,
    r.created_at,
    r.received_at
FROM dbo.inv_restock AS r
JOIN dbo.inv_restock_item AS ri
    ON ri.restock_id = r.restock_id
JOIN dbo.item_material AS m
    ON m.material_id = ri.material_id
WHERE r.restock_id = 'R9999001';

DELETE FROM dbo.inv_restock_item
WHERE restock_id = 'R9999001';

DELETE FROM dbo.inv_restock
WHERE restock_id = 'R9999001';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   十、订单 CRUD：shop_order + order_item + order_item_addon
   ============================================================================ */

PRINT N'========== 10. 订单 CRUD ==========';

SELECT
    o.order_id,
    o.channel,
    o.order_mode,
    o.order_status,
    o.member_id,
    m.name AS member_name,
    o.item_subtotal,
    o.coupon_discount,
    o.total_amount,
    o.pay_method,
    o.created_at,
    o.paid_at
FROM dbo.shop_order AS o
LEFT JOIN dbo.member_member AS m
    ON m.member_id = o.member_id
ORDER BY o.created_at DESC;

BEGIN TRANSACTION;

INSERT INTO dbo.shop_order
    (order_id, channel, order_mode, order_status,
     member_id, employee_id,
     item_subtotal, coupon_discount, total_amount,
     pay_method, pay_txn_no,
     created_at, paid_at, completed_at, refund_amount, refund_at)
VALUES
    ('209912310001', 'COUNTER', 'COUNTER_CASHIER', 'PENDING',
     'C0000002', 'E0000001',
     11.00, 0.00, 11.00,
     NULL, NULL,
     SYSDATETIME(), NULL, NULL, NULL, NULL);

INSERT INTO dbo.order_item
    (order_id, item_line, product_id, size,
     sugar_level, ice_level, temp_level,
     qty, unit_price)
VALUES
    ('209912310001', 1, 'P0000004', 'MEDIUM',
     'FULL', 'REGULAR', 'COLD',
     1, 11.00);

INSERT INTO dbo.order_item_addon
    (order_id, item_line, material_id, qty)
VALUES
    ('209912310001', 1, 'M0000001', 1);

UPDATE dbo.shop_order
SET
    order_status = 'PAID',
    pay_method = 'ONLINE',
    pay_txn_no = 'CRUD_TXN_209912310001',
    paid_at = SYSDATETIME()
WHERE order_id = '209912310001';

UPDATE dbo.shop_order
SET
    order_status = 'COMPLETED',
    completed_at = SYSDATETIME()
WHERE order_id = '209912310001';

SELECT
    o.order_id,
    o.order_status,
    o.total_amount,
    oi.item_line,
    p.name AS product_name,
    oi.size,
    oi.sugar_level,
    oi.ice_level,
    oi.temp_level,
    oi.qty,
    oi.unit_price,
    m.name AS addon_name,
    oia.qty AS addon_qty
FROM dbo.shop_order AS o
JOIN dbo.order_item AS oi
    ON oi.order_id = o.order_id
JOIN dbo.item_product AS p
    ON p.product_id = oi.product_id
LEFT JOIN dbo.order_item_addon AS oia
    ON oia.order_id = oi.order_id
   AND oia.item_line = oi.item_line
LEFT JOIN dbo.item_material AS m
    ON m.material_id = oia.material_id
WHERE o.order_id = '209912310001'
ORDER BY oi.item_line, m.name;

DELETE FROM dbo.order_item_addon
WHERE order_id = '209912310001';

DELETE FROM dbo.order_item
WHERE order_id = '209912310001';

DELETE FROM dbo.shop_order
WHERE order_id = '209912310001';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   十一、平台订单 CRUD：order_platform
   ============================================================================ */

PRINT N'========== 11. order_platform 平台订单信息 ==========';

SELECT
    op.order_id,
    op.platform_order_no,
    op.platform_name,
    op.delivery_address,
    op.delivery_fee,
    op.commission_rate,
    op.commission_amount
FROM dbo.order_platform AS op
ORDER BY op.order_id;

BEGIN TRANSACTION;

UPDATE dbo.order_platform
SET delivery_fee = 4.00
WHERE order_id = '202609160004';

SELECT *
FROM dbo.order_platform
WHERE order_id = '202609160004';

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   十二、库存流水：只增不改
   ============================================================================ */

PRINT N'========== 12. inv_stock_log 库存流水 ==========';

SELECT
    l.stock_log_id,
    l.material_id,
    m.name AS material_name,
    l.log_type,
    l.qty,
    l.order_id,
    l.restock_id,
    e.name AS employee_name,
    l.created_at
FROM dbo.inv_stock_log AS l
JOIN dbo.item_material AS m
    ON m.material_id = l.material_id
JOIN dbo.staff_employee AS e
    ON e.employee_id = l.employee_id
WHERE l.material_id = 'M0000002'
ORDER BY l.created_at DESC, l.stock_log_id DESC;

BEGIN TRANSACTION;

INSERT INTO dbo.inv_stock_log
    (material_id, log_type, qty,
     order_id, restock_id, employee_id, created_at)
VALUES
    ('M0000002', 'LOSS', 10,
     NULL, NULL, 'E0000003', SYSDATETIME());

SELECT TOP (1)
    *
FROM dbo.inv_stock_log
WHERE material_id = 'M0000002'
ORDER BY stock_log_id DESC;

ROLLBACK TRANSACTION;

/*
inv_stock_log 是历史审计流水，不能用 UPDATE / DELETE 篡改历史。
库存快照 item_material.stock_qty / reserved_qty 应由对应业务事务维护。
*/

/* ============================================================================
   十三、积分流水 / 储值流水：只增不改
   ============================================================================ */

PRINT N'========== 13. member_point_log / member_balance_log ==========';

SELECT
    l.point_log_id,
    l.member_id,
    m.name AS member_name,
    l.point_type,
    l.points,
    l.order_id,
    l.created_at
FROM dbo.member_point_log AS l
JOIN dbo.member_member AS m
    ON m.member_id = l.member_id
WHERE l.member_id = 'C0000001'
ORDER BY l.created_at DESC, l.point_log_id DESC;

SELECT
    l.balance_log_id,
    l.member_id,
    m.name AS member_name,
    l.balance_type,
    l.amount,
    l.order_id,
    l.external_ref,
    l.created_at
FROM dbo.member_balance_log AS l
JOIN dbo.member_member AS m
    ON m.member_id = l.member_id
WHERE l.member_id = 'C0000001'
ORDER BY l.created_at DESC, l.balance_log_id DESC;

/*
积分/储值流水同样属于历史账务记录。
实际业务中应通过“冲正/退款”产生新的流水，而不是 UPDATE/DELETE 旧流水。
*/

/* ============================================================================
   十四、综合业务 CRUD：会员充值
   ============================================================================ */

PRINT N'========== 14. 综合业务 CRUD：会员充值 ==========';

BEGIN TRANSACTION;

DECLARE @DemoMember CHAR(8) = 'C0000002';
DECLARE @RechargeAmount DECIMAL(10,2) = 50.00;
DECLARE @RechargeRef VARCHAR(64) = 'CRUD_RECHARGE_20991231';

INSERT INTO dbo.member_balance_log
    (member_id, balance_type, amount, order_id, external_ref, created_at)
VALUES
    (@DemoMember, 'RECHARGE', @RechargeAmount,
     NULL, @RechargeRef, SYSDATETIME());

UPDATE dbo.member_member
SET balance = balance + @RechargeAmount
WHERE member_id = @DemoMember;

INSERT INTO dbo.member_point_log
    (member_id, point_type, points, order_id, created_at)
VALUES
    (@DemoMember, 'EARN_RECHARGE',
     CONVERT(INT, FLOOR(@RechargeAmount)),
     NULL, SYSDATETIME());

UPDATE dbo.member_member
SET points = points + CONVERT(INT, FLOOR(@RechargeAmount))
WHERE member_id = @DemoMember;

SELECT
    member_id,
    name,
    points,
    balance
FROM dbo.member_member
WHERE member_id = @DemoMember;

SELECT
    balance_type,
    amount,
    external_ref,
    created_at
FROM dbo.member_balance_log
WHERE external_ref = @RechargeRef;

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   十五、综合业务 CRUD：补货入库
   ============================================================================ */

PRINT N'========== 15. 综合业务 CRUD：补货入库 ==========';

BEGIN TRANSACTION;

DECLARE @DemoRestock CHAR(8) = 'R9999002';
DECLARE @DemoMaterial CHAR(8) = 'M0000002';
DECLARE @DemoQty DECIMAL(10,2) = 200.00;

INSERT INTO dbo.inv_restock
    (restock_id, supplier_name, restock_status,
     employee_id, created_at, received_at)
VALUES
    (@DemoRestock, N'CRUD测试供应商', 'PENDING',
     'E0000003', SYSDATETIME(), NULL);

INSERT INTO dbo.inv_restock_item
    (restock_id, material_id, qty)
VALUES
    (@DemoRestock, @DemoMaterial, @DemoQty);

UPDATE dbo.inv_restock
SET
    restock_status = 'RECEIVED',
    received_at = SYSDATETIME()
WHERE restock_id = @DemoRestock;

UPDATE dbo.item_material
SET stock_qty = stock_qty + @DemoQty
WHERE material_id = @DemoMaterial;

INSERT INTO dbo.inv_stock_log
    (material_id, log_type, qty,
     order_id, restock_id, employee_id, created_at)
VALUES
    (@DemoMaterial, 'IN', @DemoQty,
     NULL, @DemoRestock, 'E0000003', SYSDATETIME());

SELECT
    m.material_id,
    m.name,
    m.stock_qty,
    r.restock_id,
    r.restock_status,
    ri.qty AS restock_qty
FROM dbo.item_material AS m
JOIN dbo.inv_restock_item AS ri
    ON ri.material_id = m.material_id
JOIN dbo.inv_restock AS r
    ON r.restock_id = ri.restock_id
WHERE r.restock_id = @DemoRestock;

ROLLBACK TRANSACTION;
GO

/* ============================================================================
   十六、综合 Read：库存可售量
   ============================================================================ */

PRINT N'========== 16. 综合查询：库存可售量 ==========';

SELECT
    material_id,
    name,
    unit,
    stock_qty,
    reserved_qty,
    stock_qty - reserved_qty AS available_qty,
    safety_stock,
    CASE
        WHEN stock_qty - reserved_qty <= safety_stock THEN N'需要补货'
        ELSE N'库存正常'
    END AS stock_status
FROM dbo.item_material
ORDER BY
    CASE
        WHEN stock_qty - reserved_qty <= safety_stock THEN 0
        ELSE 1
    END,
    material_id;

/* ============================================================================
   十七、综合 Read：订单金额与会员
   ============================================================================ */

PRINT N'========== 17. 综合查询：订单金额 ==========';

SELECT
    o.order_id,
    o.created_at,
    o.channel,
    o.order_status,
    COALESCE(m.name, N'非会员') AS member_name,
    o.item_subtotal,
    o.coupon_discount,
    o.total_amount,
    o.pay_method
FROM dbo.shop_order AS o
LEFT JOIN dbo.member_member AS m
    ON m.member_id = o.member_id
ORDER BY o.created_at DESC;

/* ============================================================================
   十八、结束
   ============================================================================ */

PRINT N'crud.sql 执行完成：所有演示写操作均已 ROLLBACK，不改变 seed 数据。';
PRINT N'库存/积分/储值流水按审计数据处理，只做 INSERT + SELECT，不提供 UPDATE/DELETE。';
GO
