/* ============================================================================
   regression.sql —— v1.0 迁移后回归（第六周 ⑤）
   ----------------------------------------------------------------------------
   对象：迁移后副本 MilkTeaShop_v01（v1.0，结构指纹 236 项）
   前置：必须先跑 sql/migration.sql 完成 v0.1 → v1.0（本脚本引用 order_item_addon
         的 addon_price 快照列，未迁移会报"列名无效"）。
   目的：证明迁移只做了三件事（+1 快照列、+15 CHECK、+1 筛选唯一索引），业务语义
         一字未改。逐项对比 result/第6周-①迁移前基准-20261018.txt。

   ★ 与迁移前基准的唯一差异：加料金额从
       SUM(oia.qty * a.addon_price)  （LEFT JOIN item_addon_option 现查单价）
       改为
       SUM(oia.qty * oia.addon_price)（读本行快照列）
       两者在 §1 做了逐行相等核对（差异必须为 0），故业务基准逐值相同是必然结论。

   结构（六段）：
     ① 快照一致性：oia.addon_price 与 item_addon_option.addon_price 逐行相等
     ② 三组业务基准（订单明细 / 商品销售 / 库存）—— 与①逐值相同
     ③ 四条口径核对（5.1 会员 / 5.2 预占 / 5.3 期初 / 5.4 金额等式）—— 差异全 0
     ④ 行数与关键金额摘要 —— 17 表行数不变
     ⑤ 结构指纹 —— 236 项 MATCH
     ⑥ 约束反例（15 CHECK + 1 索引）＋ 角色权限 13 例不变

   可重复执行：所有写操作都在事务内回滚，不留数据；只读查询可反复跑。
   执行：sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/regression.sql
        -o result/第6周-⑤回归结果-20261018.txt
   ============================================================================ */
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

USE [MilkTeaShop_v01];
GO

/* ============================================================================
   ① 快照一致性：oia.addon_price 必须与 item_addon_option 现查单价逐行相等
   ----------------------------------------------------------------------------
   这是"回填正确"的直接证明：migration.sql S1-2 用 (product_id, material_id) 联
   item_addon_option 回填快照列，故迁移后两处单价应完全一致，mismatch_count = 0。
   ============================================================================ */
PRINT N'================================================================================';
PRINT N'【①】加料单价快照一致性（oia.addon_price vs item_addon_option.addon_price，差异应为 0）';
PRINT N'================================================================================';
SELECT COUNT(*) AS addon_row_count FROM dbo.order_item_addon;

SELECT COUNT(*) AS mismatch_count
FROM dbo.order_item_addon AS oia
JOIN dbo.order_item AS oi
  ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
JOIN dbo.item_addon_option AS a
  ON a.product_id = oi.product_id AND a.material_id = oia.material_id
WHERE oia.addon_price <> a.addon_price;
GO

/* ============================================================================
   ② 三组业务基准（与①逐值相同；加料金额改用 oia.addon_price）
   ============================================================================ */
PRINT N'';
PRINT N'================================================================================';
PRINT N'【基准一】订单明细（订单头 + 明细行 + 该行加料金额）';
PRINT N'================================================================================';
WITH AddonAmount AS (
    SELECT order_id, item_line, SUM(qty * addon_price) AS addon_amount
    FROM dbo.order_item_addon
    GROUP BY order_id, item_line
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
PRINT N'【基准一·附】订单加料明细（单价读 oia.addon_price 快照列）';
PRINT N'================================================================================';
SELECT
      oia.order_id
    , oia.item_line
    , oia.material_id
    , m.name AS material_name
    , oia.qty AS addon_qty
    , oia.addon_price
    , oia.qty * oia.addon_price AS addon_amount
FROM dbo.order_item_addon AS oia
JOIN dbo.item_material AS m
  ON m.material_id = oia.material_id
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
PRINT N'================================================================================';
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

/* ============================================================================
   ③ 四条口径核对（差异应全为 0）
   ============================================================================ */
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
    SELECT order_id, item_line, SUM(qty * addon_price) AS addon_amount
    FROM dbo.order_item_addon
    GROUP BY order_id, item_line
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

/* ============================================================================
   ④ 行数与关键金额摘要（应与迁移前基准 §5.2 一致）
   ============================================================================ */
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

/* ============================================================================
   ⑤ 结构指纹（应 = 236 项）
   ============================================================================ */
PRINT N'';
PRINT N'================================================================================';
PRINT N'【结构指纹】v1.0 目标 236 项';
PRINT N'================================================================================';
SELECT
      (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) AS total_columns
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') AS total_pk
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ') AS total_uq_constraint
    , (SELECT COUNT(*) FROM sys.indexes i WHERE i.is_unique = 1 AND i.is_primary_key = 0
          AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0
          AND i.object_id IN (SELECT object_id FROM sys.tables)) AS total_uq_filtered_index
    , (SELECT COUNT(*) FROM sys.foreign_keys) AS total_fk
    , (SELECT COUNT(*) FROM sys.check_constraints) AS total_check
    , (SELECT COUNT(*) FROM sys.default_constraints) AS total_default;

SELECT
      N'111 列 / 17 主码 / 3 UQ + 4 筛选唯一索引 / 25 外码 / 60 CHECK / 16 DEFAULT = 236' AS expected
    , CASE WHEN
        (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) = 111
        AND (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') = 17
        AND (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ') = 3
        AND (SELECT COUNT(*) FROM sys.indexes i WHERE i.is_unique = 1 AND i.is_primary_key = 0
              AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0
              AND i.object_id IN (SELECT object_id FROM sys.tables)) = 4
        AND (SELECT COUNT(*) FROM sys.foreign_keys) = 25
        AND (SELECT COUNT(*) FROM sys.check_constraints) = 60
        AND (SELECT COUNT(*) FROM sys.default_constraints) = 16
      THEN N'MATCH：v1.0 目标结构 236 项'
      ELSE N'DIFF：与 docs/08/重构方案.md §4.4 目标指纹不一致'
      END AS verification_result;
GO

/* ============================================================================
   ⑥-1 约束反例：15 条新增 CHECK + 1 条筛选唯一索引
   ----------------------------------------------------------------------------
   每条在事务内执行、捕获错误号后回滚，不留数据。"应被拒"用例期望错误号：
   547（CHECK）、2601（筛选唯一索引）。
   反例均针对迁移后库里已存在的行，用实测到的单号/流水号定点构造，故确定性成立。
   ============================================================================ */
PRINT N'';
PRINT N'================================================================================';
PRINT N'【约束反例】新增 15 CHECK + 1 筛选唯一索引（期望 547 / 2601）';
PRINT N'================================================================================';
CREATE TABLE #results (
    test_id     nvarchar(10)  NOT NULL,
    description nvarchar(250) NOT NULL,
    passed      bit           NOT NULL,
    detail      nvarchar(100) NULL
);
GO

-- V01 P1：COUNTER 单 order_mode 置 NULL 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.shop_order SET order_mode = NULL WHERE order_id = '202609160001';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V01', N'CK_shop_order_order_mode_channel：COUNTER 单 order_mode 置 NULL 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V02 P2：PENDING 单置 pay_method 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.shop_order SET pay_method = 'CASH' WHERE order_id = '202609160005';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V02', N'CK_shop_order_pending：PENDING 单置 pay_method 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V03 P3：SALE 流水 order_id 为 NULL 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at)
        VALUES ('M0000001', 'SALE', 1, NULL, NULL, 'E0000003', '2026-09-16 10:00:00');
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V03', N'CK_inv_stock_log_order：SALE 流水无订单应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V04 P4：IN 流水 restock_id 为 NULL 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at)
        VALUES ('M0000001', 'IN', 1, NULL, NULL, 'E0000003', '2026-09-16 10:00:00');
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V04', N'CK_inv_stock_log_restock：IN 流水无补货单应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V05 P5：SPEND 流水 order_id 为 NULL 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.member_balance_log (member_id, balance_type, amount, order_id, created_at)
        VALUES ('C0000001', 'SPEND', 10.00, NULL, '2026-09-16 10:00:00');
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V05', N'CK_member_balance_log_order：SPEND 流水无订单应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V06 P6：USED 券 used_order_id 置 NULL 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.mkt_member_coupon SET used_order_id = NULL WHERE member_coupon_id = 2;
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V06', N'CK_mkt_member_coupon_used：USED 券 used_order_id 置 NULL 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V07 P7：两券核销到同一单（used_order_id 重复）应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.mkt_member_coupon SET used_order_id = '202609160002' WHERE member_coupon_id = 1;
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V07', N'UQ_mkt_member_coupon_used_order：used_order_id 重复应被拒（期望 2601）', CASE WHEN @e = 2601 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V08 P8：商品售价置 0 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.item_product SET unit_price = 0 WHERE product_id = 'P0000001';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V08', N'CK_item_product_unit_price：商品售价置 0 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V09 P8：订单行成交价置 0 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.order_item SET unit_price = 0 WHERE order_id = '202609160001' AND item_line = 1;
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V09', N'CK_order_item_unit_price：订单行成交价置 0 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V10 P8：加料快照单价置负应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.order_item_addon SET addon_price = -1;
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V10', N'CK_order_item_addon_addon_price：加料快照单价置负应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V11 P8：平台佣金率 > 1 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.order_platform SET commission_rate = 1.5000 WHERE order_id = '202609160004';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V11', N'CK_order_platform_commission_rate：佣金率 > 1 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V12 P8：平台佣金金额置负应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.order_platform SET commission_amount = -1 WHERE order_id = '202609160004';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V12', N'CK_order_platform_commission_amount：佣金金额置负应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V13 P8：券前金额置负应被拒（同时触发 P12，仍为 547）
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.shop_order SET item_subtotal = -1 WHERE order_id = '202609160001';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V13', N'CK_shop_order_item_subtotal：券前金额置负应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V14 P8：券优惠置负应被拒（同时触发 P12，仍为 547）
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.shop_order SET coupon_discount = -1 WHERE order_id = '202609160001';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V14', N'CK_shop_order_coupon_discount：券优惠置负应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V15 P8：退款金额置负应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.shop_order SET refund_amount = -1 WHERE order_id = '202609160006';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V15', N'CK_shop_order_refund_amount：退款金额置负应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

-- V16 P12：total_amount ≠ item_subtotal − coupon_discount 应被拒
DECLARE @e int = 0;
BEGIN TRAN;
    BEGIN TRY
        UPDATE dbo.shop_order SET total_amount = 33.00 WHERE order_id = '202609160001';
    END TRY BEGIN CATCH
        SET @e = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'V16', N'CK_shop_order_total_amount_eq：total ≠ subtotal − discount 应被拒（期望 547）', CASE WHEN @e = 547 THEN 1 ELSE 0 END, CAST(@e AS nvarchar(20)));
GO

/* 约束反例汇总 */
SELECT
      COUNT(*)                                               AS total_tests
    , SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END)            AS passed_count
    , SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END)            AS failed_count
    , CASE WHEN SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) = 0
             THEN N'ALL CONSTRAINT TESTS PASSED' ELSE N'HAS FAILURES' END AS result
FROM #results;

SELECT test_id, description, passed, detail
FROM #results
ORDER BY test_id;

DROP TABLE #results;
GO

/* ============================================================================
   ⑥-2 角色权限：13 例（P01–P06 正例 + N01–N07 反例）应保持不变
   ----------------------------------------------------------------------------
   迁移只加了 order_item_addon 的列（表级 GRANT 已覆盖新列）和 CHECK 约束（不影
   响权限），故 role.sql 的 13 例在 v01 上结果应与迁移前一致。期望错误号：
   229（缺 SELECT/INSERT/UPDATE/DELETE 权限）、230（列级 UPDATE 拒绝）、262（DDL）。
   ============================================================================ */
PRINT N'';
PRINT N'================================================================================';
PRINT N'【角色权限】正/反例 13 项（应与迁移前一致，期望 229/230/262）';
PRINT N'================================================================================';
CREATE TABLE #role_results (
    test_id     nvarchar(10)  NOT NULL,
    description nvarchar(250) NOT NULL,
    passed      bit           NOT NULL,
    detail      nvarchar(100) NULL
);
GO

-- P01 收银员能读商品（点单前提）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    SELECT TOP (1) product_id FROM dbo.item_product;
    SET @ok = 1;
END TRY
BEGIN CATCH
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'P01', N'收银员读商品应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P02 收银员能下单（写 shop_order；新约束 P1/P2/P12 下仍合法）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    BEGIN TRAN;
    INSERT INTO dbo.shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, item_subtotal, coupon_discount, total_amount, pay_method, created_at)
    VALUES ('202609169999', 'COUNTER', 'COUNTER_CASHIER', 'PENDING', NULL, 'E0000001', 10.00, 0.00, 10.00, NULL, '2026-09-16 10:00:00');
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'P02', N'收银员下单应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P03 制作员能改订单状态（列级授权）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_maker';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.shop_order SET order_status = 'MAKING' WHERE order_id = '202609160005';
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'P03', N'制作员改订单状态应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P04 库存管理员能改库存量
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.item_material SET stock_qty = stock_qty + 1 WHERE material_id = 'M0000001';
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'P04', N'库存管理员改库存量应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P05 库存管理员能记库存流水（LOSS 流水，新约束 P3/P4 下仍合法）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    BEGIN TRAN;
    INSERT INTO dbo.inv_stock_log (material_id, log_type, qty, order_id, restock_id, employee_id, created_at)
    VALUES ('M0000001', 'LOSS', 1, NULL, NULL, 'E0000003', '2026-09-16 10:00:00');
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'P05', N'库存管理员记库存流水应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- P06 店长能维护商品（新增商品，新约束 P8 下仍合法）
DECLARE @ok bit = 1, @err int = 0;
EXECUTE AS USER = 'u_manager';
BEGIN TRY
    BEGIN TRAN;
    INSERT INTO dbo.item_product (product_id, name, size, unit_price) VALUES ('P9999000', N'测试新品', 'MEDIUM', 9.00);
    ROLLBACK TRAN;
    SET @ok = 1;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @ok = 0; SET @err = ERROR_NUMBER();
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'P06', N'店长新增商品应成功', @ok, CAST(@err AS nvarchar(20)));
GO

-- N01 收银员改库存应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.item_material SET stock_qty = 0 WHERE material_id = 'M0000001';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N01', N'收银员改库存应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N02 收银员改会员余额应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_cashier';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.member_member SET balance = 999 WHERE member_id = 'C0000001';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N02', N'收银员改会员余额应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N03 制作员读会员表应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_maker';
BEGIN TRY
    SELECT * FROM dbo.member_member;
    SET @ok = 0;
END TRY
BEGIN CATCH
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N03', N'制作员读会员表应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N04 制作员改订单金额（非状态列）应被拒（列级 UPDATE 拒绝报 230）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_maker';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.shop_order SET total_amount = 0 WHERE order_id = '202609160005';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 230 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N04', N'制作员改订单金额应被拒（期望 230）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N05 库存管理员读会员表应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    SELECT * FROM dbo.member_member;
    SET @ok = 0;
END TRY
BEGIN CATCH
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N05', N'库存管理员读会员表应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N06 库存管理员改商品成交价应被拒（229）
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_stock_keeper';
BEGIN TRY
    BEGIN TRAN;
    UPDATE dbo.item_product SET unit_price = 1.00 WHERE product_id = 'P0000001';
    ROLLBACK TRAN;
    SET @ok = 0;
END TRY
BEGIN CATCH
    IF @@TRANCOUNT > 0 ROLLBACK TRAN;
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 229 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N06', N'库存管理员改成交价应被拒（期望 229）', @ok, CAST(@err AS nvarchar(20)));
GO

-- N07 店长建表（DDL）应被拒（262）—— 业务管理 ≠ DBA
DECLARE @ok bit = 0, @err int = 0;
EXECUTE AS USER = 'u_manager';
BEGIN TRY
    CREATE TABLE dbo.__role_test (x int);
    DROP TABLE dbo.__role_test;
    SET @ok = 0;
END TRY
BEGIN CATCH
    SET @err = ERROR_NUMBER();
    SET @ok = CASE WHEN ERROR_NUMBER() = 262 THEN 1 ELSE 0 END;
END CATCH;
REVERT;
INSERT INTO #role_results VALUES (N'N07', N'店长建表（DDL）应被拒（期望 262）', @ok, CAST(@err AS nvarchar(20)));
GO

/* 角色权限汇总 */
SELECT
      COUNT(*)                                               AS total_tests
    , SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END)            AS passed_count
    , SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END)            AS failed_count
    , CASE WHEN SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) = 0
             THEN N'ALL ROLE TESTS PASSED' ELSE N'HAS FAILURES' END AS result
FROM #role_results;

SELECT test_id, description, passed, detail
FROM #role_results
ORDER BY test_id;

DROP TABLE #role_results;
GO

PRINT N'';
PRINT N'regression.sql：迁移后回归完成——业务基准/口径/行数/指纹/约束反例/角色权限六段均已核对。';
PRINT N'对比基线：result/第6周-①迁移前基准-20261018.txt（业务数据应逐值相同，结构指纹 219 → 236）。';
GO
