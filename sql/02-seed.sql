-- =====================================================================
-- 02-seed.sql  样例数据（虚构）
-- 面向空库、在 01-schema.sql 之后执行；不删表、不重建，按外码依赖顺序插入。
-- 覆盖：到店现金/余额/线上、平台单、待支付预占、两条整单退款、补货入库、损耗、
--       积分计分与冲正、储值充值/消费/退回、券的未使用/已核销/已退回。
-- 注意：BIGINT IDENTITY 列（流水/持券主键）不显式插入，由 DB 自动生成。
-- =====================================================================
SET NOCOUNT ON;
GO

-- ---------- 主数据 ----------

-- 员工
INSERT INTO staff_employee (employee_id, name, job_title, employ_status) VALUES
('E0000001', N'张伟', 'CASHIER',      'ACTIVE'),
('E0000002', N'李娜', 'MAKER',        'ACTIVE'),
('E0000003', N'王强', 'STOCK_KEEPER', 'ACTIVE'),
('E0000004', N'赵敏', 'MANAGER',      'ACTIVE');
GO

-- 原料 / 加料（kind 区分；stock_qty/reserved_qty 为当前快照，已反映历史流水）
INSERT INTO item_material (material_id, name, unit, material_kind, stock_qty, reserved_qty, safety_stock, shelf_life_days) VALUES
('M0000001', N'珍珠', N'克',   'ADDON',      4500,  30, 2000, 30),
('M0000002', N'鲜奶', N'毫升', 'INGREDIENT', 2600, 150, 1000, 7),
('M0000003', N'茶叶', N'克',   'INGREDIENT', 1900,   5,  500, 90),
('M0000004', N'椰果', N'克',   'ADDON',      2900,  25, 1000, 30),
('M0000005', N'布丁', N'克',   'ADDON',      1900,   0,  800, 15),
('M0000006', N'蔗糖', N'克',   'INGREDIENT', 9800,  20, 3000, NULL),
('M0000007', N'杯具', N'个',   'INGREDIENT', 4900,   1, 1000, NULL);
GO

-- 商品（杯型为商品固有属性：不同杯型不同商品不同价）
INSERT INTO item_product (product_id, name, size, unit_price, sale_status) VALUES
('P0000001', N'珍珠奶茶', 'MEDIUM', 12.00, 'ON_SALE'),
('P0000002', N'珍珠奶茶', 'LARGE',  15.00, 'ON_SALE'),
('P0000003', N'四季春茶', 'MEDIUM',  8.00, 'ON_SALE'),
('P0000004', N'椰果奶茶', 'MEDIUM', 11.00, 'ON_SALE'),
('P0000005', N'布丁奶茶', 'MEDIUM', 12.00, 'ON_SALE');
GO

-- 配方（每杯标准用量；单位继承原料表）
INSERT INTO item_recipe (product_id, material_id, qty) VALUES
('P0000001', 'M0000001', 30),   -- 珍珠奶茶中杯：珍珠
('P0000001', 'M0000002', 150),  -- 鲜奶
('P0000001', 'M0000003', 5),
('P0000001', 'M0000006', 20),
('P0000001', 'M0000007', 1),
('P0000002', 'M0000001', 40),   -- 珍珠奶茶大杯
('P0000002', 'M0000002', 200),
('P0000002', 'M0000003', 6),
('P0000002', 'M0000006', 25),
('P0000002', 'M0000007', 1),
('P0000003', 'M0000003', 8),    -- 四季春茶（珍珠是它的加料，不进配方）
('P0000003', 'M0000006', 15),
('P0000003', 'M0000007', 1),
('P0000004', 'M0000004', 25),   -- 椰果奶茶
('P0000004', 'M0000002', 150),
('P0000004', 'M0000003', 5),
('P0000004', 'M0000006', 20),
('P0000004', 'M0000007', 1),
('P0000005', 'M0000005', 30),   -- 布丁奶茶
('P0000005', 'M0000002', 150),
('P0000005', 'M0000003', 5),
('P0000005', 'M0000006', 20),
('P0000005', 'M0000007', 1);
GO

-- 商品-加料选项（每份加料单价 + 每份用量）
INSERT INTO item_addon_option (product_id, material_id, addon_price, addon_qty, addon_status) VALUES
('P0000003', 'M0000001', 2.00, 30, 'AVAILABLE'),  -- 四季春 + 珍珠 2元/30克
('P0000003', 'M0000004', 1.00, 25, 'AVAILABLE'),  -- 四季春 + 椰果
('P0000003', 'M0000005', 1.50, 30, 'AVAILABLE'),  -- 四季春 + 布丁
('P0000001', 'M0000004', 1.00, 25, 'AVAILABLE'),  -- 珍珠奶茶 + 椰果
('P0000004', 'M0000001', 2.00, 30, 'AVAILABLE');  -- 椰果奶茶 + 珍珠
GO

-- 会员（积分/余额为当前快照，与流水一致）
INSERT INTO member_member (member_id, phone, name, points, balance, created_at) VALUES
('C0000001', '13800000001', N'陈晓', 118, 77.00, '2026-09-10 10:00:00'),
('C0000002', '13800000002', N'刘洋',   0,  0.00, '2026-09-12 14:30:00'),
('C0000003', '13800000003', N'王芳',   0,  0.00, '2026-09-15 09:00:00');
GO

-- 券规则（满减券，固定起止日期）
INSERT INTO mkt_coupon_rule (coupon_rule_id, coupon_name, coupon_type, threshold_amount, discount_amount, valid_from, valid_to) VALUES
('CP000001', N'满30减5', 'FULL_REDUCTION', 30.00, 5.00, '2026-09-01', '2026-10-31'),
('CP000002', N'满20减3', 'FULL_REDUCTION', 20.00, 3.00, '2026-09-01', '2026-09-30');
GO

-- ---------- 补货与入库 ----------

INSERT INTO inv_restock (restock_id, supplier_name, restock_status, employee_id, created_at, received_at) VALUES
('R0000001', N'供应商A', 'RECEIVED', 'E0000003', '2026-09-15 09:00:00', '2026-09-15 14:00:00'),
('R0000002', N'供应商B', 'PENDING',  'E0000003', '2026-09-16 17:00:00', NULL);
GO

INSERT INTO inv_restock_item (restock_id, material_id, qty) VALUES
('R0000001', 'M0000001', 2000),
('R0000001', 'M0000002', 3000),
('R0000001', 'M0000003', 1000),
('R0000002', 'M0000004', 1000),
('R0000002', 'M0000005', 800);
GO

-- 入库流水（R0000001 已验收 → IN）
INSERT INTO inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at) VALUES
('M0000001', 'IN', 2000, NULL, 'R0000001', 'E0000003', '2026-09-15 14:00:00'),
('M0000002', 'IN', 3000, NULL, 'R0000001', 'E0000003', '2026-09-15 14:00:00'),
('M0000003', 'IN', 1000, NULL, 'R0000001', 'E0000003', '2026-09-15 14:00:00');
GO

-- 损耗（鲜奶临期报损 50 毫升，负向，无订单/补货单）
INSERT INTO inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at) VALUES
('M0000002', 'LOSS', 50, NULL, NULL, 'E0000003', '2026-09-15 18:00:00');
GO

-- ---------- 订单 ----------

-- 单1 到店·现金·非会员
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160001', 'COUNTER', 'COUNTER_CASHIER', 'COMPLETED', NULL, 'E0000001', 34.00, 0.00, 34.00, 'CASH', NULL, '2026-09-16 09:15:00', '2026-09-16 09:16:00', '2026-09-16 09:25:00', NULL, NULL);
-- 单2 到店·自助·会员·余额·用券(满20减3)
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160002', 'COUNTER', 'COUNTER_SELF', 'COMPLETED', 'C0000001', NULL, 26.00, 3.00, 23.00, 'BALANCE', NULL, '2026-09-16 11:00:00', '2026-09-16 11:02:00', '2026-09-16 11:15:00', NULL, NULL);
-- 单3 到店·自助·会员·线上·加料(计分)
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160003', 'COUNTER', 'COUNTER_SELF', 'COMPLETED', 'C0000001', NULL, 18.00, 0.00, 18.00, 'ONLINE', 'WX202609161001', '2026-09-16 12:30:00', '2026-09-16 12:31:00', '2026-09-16 12:45:00', NULL, NULL);
-- 单4 平台·美团·非会员(平台预收款，无 pay_method，接单即扣料)
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160004', 'PLATFORM', NULL, 'COMPLETED', NULL, 'E0000001', 24.00, 0.00, 24.00, NULL, NULL, '2026-09-16 13:10:00', '2026-09-16 13:10:00', '2026-09-16 13:40:00', NULL, NULL);
-- 单5 到店·自助·会员·待支付(预占原料，不写流水)
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160005', 'COUNTER', 'COUNTER_SELF', 'PENDING', 'C0000002', NULL, 13.00, 0.00, 13.00, NULL, NULL, '2026-09-16 14:00:00', NULL, NULL, NULL, NULL);
-- 单6 到店·线上·整单退款(冲正积分)
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160006', 'COUNTER', 'COUNTER_CASHIER', 'REFUNDED', 'C0000003', 'E0000001', 12.00, 0.00, 12.00, 'ONLINE', 'ALI202609160002', '2026-09-16 15:00:00', '2026-09-16 15:01:00', NULL, 12.00, '2026-09-16 15:30:00');
-- 单7 到店·余额·用券(满30减5)·整单退款(余额退回、券退回)
INSERT INTO shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no, created_at, paid_at, completed_at, refund_amount, refund_at) VALUES
('202609160007', 'COUNTER', 'COUNTER_CASHIER', 'REFUNDED', 'C0000001', 'E0000001', 34.00, 5.00, 29.00, 'BALANCE', NULL, '2026-09-16 16:00:00', '2026-09-16 16:01:00', NULL, 29.00, '2026-09-16 16:20:00');
GO

-- 平台订单信息（仅单4）
INSERT INTO order_platform (order_id, platform_order_no, platform_name, delivery_address, delivery_fee, commission_rate, commission_amount) VALUES
('202609160004', 'MT2026091600987', 'MEITUAN', N'校园路1号202室', 3.00, 0.2000, 4.80);
GO

-- 订单明细
INSERT INTO order_item (order_id, item_line, product_id, size, sugar_level, ice_level, temp_level, qty, unit_price) VALUES
('202609160001', 1, 'P0000001', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 2, 12.00),
('202609160001', 2, 'P0000003', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1,  8.00),
('202609160002', 1, 'P0000004', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 11.00),
('202609160002', 2, 'P0000002', 'LARGE',  'FULL', 'REGULAR', 'COLD', 1, 15.00),
('202609160003', 1, 'P0000003', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 2,  8.00),
('202609160004', 1, 'P0000005', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 12.00),
('202609160004', 2, 'P0000001', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 12.00),
('202609160005', 1, 'P0000004', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 11.00),
('202609160006', 1, 'P0000005', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 12.00),
('202609160007', 1, 'P0000002', 'LARGE',  'FULL', 'REGULAR', 'COLD', 1, 15.00),
('202609160007', 2, 'P0000004', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1, 11.00),
('202609160007', 3, 'P0000003', 'MEDIUM', 'FULL', 'REGULAR', 'COLD', 1,  8.00);
GO

-- 加料明细（qty = 该行该加料总份数）
INSERT INTO order_item_addon (order_id, item_line, material_id, qty) VALUES
('202609160001', 2, 'M0000001', 1),  -- 单1 四季春 + 珍珠×1
('202609160003', 1, 'M0000004', 2),  -- 单3 四季春×2 各加椰果×1 → 总2份
('202609160005', 1, 'M0000001', 1);  -- 单5 椰果奶茶 + 珍珠×1（待支付，仅预占）
GO

-- 销售扣料流水（已支付单；单5 PENDING 不写）
INSERT INTO inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at) VALUES
-- 单1：珍珠90(60配方+30加料) 鲜奶300 茶叶18 蔗糖55 杯具3
('M0000001', 'SALE',  90, '202609160001', NULL, 'E0000001', '2026-09-16 09:16:00'),
('M0000002', 'SALE', 300, '202609160001', NULL, 'E0000001', '2026-09-16 09:16:00'),
('M0000003', 'SALE',  18, '202609160001', NULL, 'E0000001', '2026-09-16 09:16:00'),
('M0000006', 'SALE',  55, '202609160001', NULL, 'E0000001', '2026-09-16 09:16:00'),
('M0000007', 'SALE',   3, '202609160001', NULL, 'E0000001', '2026-09-16 09:16:00'),
-- 单2：珍珠40 椰果25 鲜奶350 茶叶11 蔗糖45 杯具2
('M0000001', 'SALE',  40, '202609160002', NULL, 'E0000001', '2026-09-16 11:02:00'),
('M0000004', 'SALE',  25, '202609160002', NULL, 'E0000001', '2026-09-16 11:02:00'),
('M0000002', 'SALE', 350, '202609160002', NULL, 'E0000001', '2026-09-16 11:02:00'),
('M0000003', 'SALE',  11, '202609160002', NULL, 'E0000001', '2026-09-16 11:02:00'),
('M0000006', 'SALE',  45, '202609160002', NULL, 'E0000001', '2026-09-16 11:02:00'),
('M0000007', 'SALE',   2, '202609160002', NULL, 'E0000001', '2026-09-16 11:02:00'),
-- 单3：茶叶16 蔗糖30 杯具2 椰果50(加料)
('M0000003', 'SALE',  16, '202609160003', NULL, 'E0000001', '2026-09-16 12:31:00'),
('M0000006', 'SALE',  30, '202609160003', NULL, 'E0000001', '2026-09-16 12:31:00'),
('M0000007', 'SALE',   2, '202609160003', NULL, 'E0000001', '2026-09-16 12:31:00'),
('M0000004', 'SALE',  50, '202609160003', NULL, 'E0000001', '2026-09-16 12:31:00'),
-- 单4：布丁30 珍珠30 鲜奶300 茶叶10 蔗糖40 杯具2
('M0000005', 'SALE',  30, '202609160004', NULL, 'E0000001', '2026-09-16 13:10:00'),
('M0000001', 'SALE',  30, '202609160004', NULL, 'E0000001', '2026-09-16 13:10:00'),
('M0000002', 'SALE', 300, '202609160004', NULL, 'E0000001', '2026-09-16 13:10:00'),
('M0000003', 'SALE',  10, '202609160004', NULL, 'E0000001', '2026-09-16 13:10:00'),
('M0000006', 'SALE',  40, '202609160004', NULL, 'E0000001', '2026-09-16 13:10:00'),
('M0000007', 'SALE',   2, '202609160004', NULL, 'E0000001', '2026-09-16 13:10:00'),
-- 单6：布丁30 鲜奶150 茶叶5 蔗糖20 杯具1
('M0000005', 'SALE',  30, '202609160006', NULL, 'E0000001', '2026-09-16 15:01:00'),
('M0000002', 'SALE', 150, '202609160006', NULL, 'E0000001', '2026-09-16 15:01:00'),
('M0000003', 'SALE',   5, '202609160006', NULL, 'E0000001', '2026-09-16 15:01:00'),
('M0000006', 'SALE',  20, '202609160006', NULL, 'E0000001', '2026-09-16 15:01:00'),
('M0000007', 'SALE',   1, '202609160006', NULL, 'E0000001', '2026-09-16 15:01:00'),
-- 单7：珍珠40 椰果25 鲜奶350 茶叶19 蔗糖60 杯具3
('M0000001', 'SALE',  40, '202609160007', NULL, 'E0000001', '2026-09-16 16:01:00'),
('M0000004', 'SALE',  25, '202609160007', NULL, 'E0000001', '2026-09-16 16:01:00'),
('M0000002', 'SALE', 350, '202609160007', NULL, 'E0000001', '2026-09-16 16:01:00'),
('M0000003', 'SALE',  19, '202609160007', NULL, 'E0000001', '2026-09-16 16:01:00'),
('M0000006', 'SALE',  60, '202609160007', NULL, 'E0000001', '2026-09-16 16:01:00'),
('M0000007', 'SALE',   3, '202609160007', NULL, 'E0000001', '2026-09-16 16:01:00');
GO

-- ---------- 会员积分 / 储值流水 ----------

-- 积分流水：充值计分100 + 消费计分18 + (退款冲正12、退款计分12 相抵为0)
INSERT INTO member_point_log (member_id, point_type, points, order_id, created_at) VALUES
('C0000001', 'EARN_RECHARGE', 100, NULL,             '2026-09-15 12:00:00'),
('C0000001', 'EARN_SALE',      18, '202609160003',  '2026-09-16 12:31:00'),
('C0000003', 'EARN_SALE',      12, '202609160006',  '2026-09-16 15:01:00'),
('C0000003', 'REVERSAL',       12, '202609160006',  '2026-09-16 15:30:00');
GO

-- 储值流水：充值100 + 消费23 + 消费29 + 退款退回29
INSERT INTO member_balance_log (member_id, balance_type, amount, order_id, external_ref, created_at) VALUES
('C0000001', 'RECHARGE', 100.00, NULL,            'RECHG202609150001', '2026-09-15 12:00:00'),
('C0000001', 'SPEND',     23.00, '202609160002', NULL,                  '2026-09-16 11:02:00'),
('C0000001', 'SPEND',     29.00, '202609160007', NULL,                  '2026-09-16 16:01:00'),
('C0000001', 'REFUND',    29.00, '202609160007', NULL,                  '2026-09-16 16:20:00');
GO

-- 会员持券：UNUSED / USED / RETURNED 三态（LOCKED 为下单到支付间的瞬态，静态快照不体现）
INSERT INTO mkt_member_coupon (coupon_rule_id, member_id, coupon_status, used_order_id, issued_at) VALUES
('CP000001', 'C0000001', 'RETURNED', '202609160007', '2026-09-11 10:00:00'),
('CP000002', 'C0000001', 'USED',     '202609160002', '2026-09-11 10:00:00'),
('CP000001', 'C0000002', 'UNUSED',   NULL,           '2026-09-12 14:30:00');
GO

PRINT '02-seed.sql 执行完成。';
GO
