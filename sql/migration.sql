/* ============================================================================
   migration.sql —— v0.1 → v1.0 结构迁移（第六周 ④）
   ----------------------------------------------------------------------------
   对象：迁移副本 MilkTeaShop_v01（v0.1，结构指纹 219 项）
   前置：副本须是干净的 v0.1——按 sql/README.md 从空库重建 MilkTeaShop 后，
         BACKUP/RESTORE 复制为 MilkTeaShop_v01（见 result/第6周-①迁移前基准-说明.md §3）。
   作用：按 docs/08-规范化/重构方案.md §4 实施——
         · S1  order_item_addon 增加 addon_price 快照列（+1 列）
         · 15 条 CHECK（P1–P8、P12；P1 因与 v0.1 枚举约束同名，改用
           CK_shop_order_order_mode_channel）
         · 1 条筛选唯一索引（P7）
   结果：结构指纹 219 → 236 项
         （111 列 + 17 主码 + 3 UQ + 4 筛选唯一索引 + 25 外码 + 60 CHECK + 16 DEFAULT）
   纪律：只跑一次；源库 MilkTeaShop 全程不动。失败即整体回滚，需从干净副本重新开始。
   执行：sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/migration.sql
   ============================================================================ */
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

USE [MilkTeaShop_v01];
GO

/* ----------------------------------------------------------------------------
   预检：确认是干净 v0.1，且现有数据全部满足新增约束（任一不满足即中止）。
   ---------------------------------------------------------------------------- */
DECLARE @cols int = (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id);
DECLARE @ck   int = (SELECT COUNT(*) FROM sys.check_constraints);
DECLARE @has_addon_price int = (SELECT COUNT(*) FROM sys.columns
                                 WHERE object_id = OBJECT_ID('dbo.order_item_addon') AND name = 'addon_price');

IF @cols <> 110 OR @ck <> 45
    THROW 51200, N'预检失败：副本不是干净的 v0.1（列数应为 110、CHECK 应为 45）。请从 MilkTeaShop 重新还原副本。', 1;
IF @has_addon_price > 0
    THROW 51201, N'预检失败：order_item_addon 已存在 addon_price 列，本副本已迁移过。请从 MilkTeaShop 重新还原副本。', 1;

DECLARE @violations int = 0;
SELECT @violations = @violations + COUNT(*) FROM dbo.shop_order
 WHERE NOT ((channel = 'PLATFORM' AND order_mode IS NULL)
         OR (channel = 'COUNTER'  AND order_mode IS NOT NULL));            -- P1
SELECT @violations = @violations + COUNT(*) FROM dbo.shop_order
 WHERE NOT (order_status <> 'PENDING' OR pay_method IS NULL);             -- P2
SELECT @violations = @violations + COUNT(*) FROM dbo.inv_stock_log
 WHERE NOT ((log_type = 'SALE' AND order_id IS NOT NULL)
         OR (log_type <> 'SALE' AND order_id IS NULL));                   -- P3
SELECT @violations = @violations + COUNT(*) FROM dbo.inv_stock_log
 WHERE NOT ((log_type = 'IN' AND restock_id IS NOT NULL)
         OR (log_type <> 'IN' AND restock_id IS NULL));                   -- P4
SELECT @violations = @violations + COUNT(*) FROM dbo.member_balance_log
 WHERE NOT ((balance_type = 'RECHARGE' AND order_id IS NULL)
         OR (balance_type <> 'RECHARGE' AND order_id IS NOT NULL));       -- P5
SELECT @violations = @violations + COUNT(*) FROM dbo.mkt_member_coupon
 WHERE NOT ((coupon_status = 'UNUSED' AND used_order_id IS NULL)
         OR (coupon_status <> 'UNUSED' AND used_order_id IS NOT NULL));   -- P6
SELECT @violations = @violations + COUNT(*) FROM (
    SELECT used_order_id FROM dbo.mkt_member_coupon
     WHERE used_order_id IS NOT NULL GROUP BY used_order_id HAVING COUNT(*) > 1) AS dup;  -- P7
SELECT @violations = @violations + COUNT(*) FROM dbo.item_product  WHERE unit_price <= 0;  -- P8-1
SELECT @violations = @violations + COUNT(*) FROM dbo.order_item    WHERE unit_price <= 0;  -- P8-2
SELECT @violations = @violations + COUNT(*) FROM dbo.shop_order
 WHERE item_subtotal < 0 OR coupon_discount < 0;                             -- P8-3/4
SELECT @violations = @violations + COUNT(*) FROM dbo.shop_order WHERE refund_amount < 0;    -- P8-5
SELECT @violations = @violations + COUNT(*) FROM dbo.order_platform
 WHERE commission_rate < 0 OR commission_rate > 1;                           -- P8-6
SELECT @violations = @violations + COUNT(*) FROM dbo.order_platform WHERE commission_amount < 0; -- P8-7
SELECT @violations = @violations + COUNT(*) FROM dbo.shop_order
 WHERE total_amount <> item_subtotal - coupon_discount;                      -- P12
SELECT @violations = @violations + COUNT(*) FROM dbo.order_item_addon AS oia
 LEFT JOIN dbo.order_item AS oi
   ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
 LEFT JOIN dbo.item_addon_option AS a
   ON a.product_id = oi.product_id AND a.material_id = oia.material_id
 WHERE a.addon_price IS NULL;                                                -- S1 回填前提

IF @violations > 0
BEGIN
    DECLARE @msg_viol nvarchar(400) =
        N'预检失败：现有数据存在 ' + CAST(@violations AS nvarchar(10)) + N' 条不满足新增约束的行。请先修数据。';
    THROW 51202, @msg_viol, 1;
END
GO

/* ----------------------------------------------------------------------------
   迁移（事务内；XACT_ABORT ON 下任一语句失败即整体回滚）。
   列新增与后续引用分属不同 batch，避免"同一批内新列尚未可见"的编译问题。
   ---------------------------------------------------------------------------- */
SET XACT_ABORT ON;
BEGIN TRANSACTION;

/* S1-1：先加可空列 */
ALTER TABLE dbo.order_item_addon ADD addon_price DECIMAL(10,2) NULL;
GO

/* S1-2：用当前加料价回填（本库样例价格自 seed 未变；真实环境须用历史价回填） */
UPDATE oia
SET addon_price = a.addon_price
FROM dbo.order_item_addon AS oia
JOIN dbo.order_item AS oi
  ON oi.order_id = oia.order_id AND oi.item_line = oia.item_line
JOIN dbo.item_addon_option AS a
  ON a.product_id = oi.product_id AND a.material_id = oia.material_id;

IF EXISTS (SELECT 1 FROM dbo.order_item_addon WHERE addon_price IS NULL)
    THROW 51203, N'迁移失败：回填后仍有 NULL 的 addon_price（存在无匹配加料选项的行）。', 1;
GO

/* S1-3：确认无 NULL 后收紧为 NOT NULL */
ALTER TABLE dbo.order_item_addon ALTER COLUMN addon_price DECIMAL(10,2) NOT NULL;
GO

/* 15 条 CHECK + 1 条筛选唯一索引（P1–P8、P12） */
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_order_mode_channel
    CHECK ((channel = 'PLATFORM' AND order_mode IS NULL)
        OR (channel = 'COUNTER'  AND order_mode IS NOT NULL));
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_pending
    CHECK (order_status <> 'PENDING' OR pay_method IS NULL);
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_item_subtotal
    CHECK (item_subtotal >= 0);
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_coupon_discount
    CHECK (coupon_discount >= 0);
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_refund_amount
    CHECK (refund_amount >= 0);
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_total_amount_eq
    CHECK (total_amount = item_subtotal - coupon_discount);

ALTER TABLE dbo.item_product ADD CONSTRAINT CK_item_product_unit_price
    CHECK (unit_price > 0);
ALTER TABLE dbo.order_item ADD CONSTRAINT CK_order_item_unit_price
    CHECK (unit_price > 0);
ALTER TABLE dbo.order_item_addon ADD CONSTRAINT CK_order_item_addon_addon_price
    CHECK (addon_price > 0);

ALTER TABLE dbo.order_platform ADD CONSTRAINT CK_order_platform_commission_rate
    CHECK (commission_rate >= 0 AND commission_rate <= 1);
ALTER TABLE dbo.order_platform ADD CONSTRAINT CK_order_platform_commission_amount
    CHECK (commission_amount >= 0);

ALTER TABLE dbo.inv_stock_log ADD CONSTRAINT CK_inv_stock_log_order
    CHECK ((log_type = 'SALE' AND order_id IS NOT NULL)
        OR (log_type <> 'SALE' AND order_id IS NULL));
ALTER TABLE dbo.inv_stock_log ADD CONSTRAINT CK_inv_stock_log_restock
    CHECK ((log_type = 'IN' AND restock_id IS NOT NULL)
        OR (log_type <> 'IN' AND restock_id IS NULL));

ALTER TABLE dbo.member_balance_log ADD CONSTRAINT CK_member_balance_log_order
    CHECK ((balance_type = 'RECHARGE' AND order_id IS NULL)
        OR (balance_type <> 'RECHARGE' AND order_id IS NOT NULL));

ALTER TABLE dbo.mkt_member_coupon ADD CONSTRAINT CK_mkt_member_coupon_used
    CHECK ((coupon_status = 'UNUSED' AND used_order_id IS NULL)
        OR (coupon_status <> 'UNUSED' AND used_order_id IS NOT NULL));
CREATE UNIQUE INDEX UQ_mkt_member_coupon_used_order
    ON dbo.mkt_member_coupon (used_order_id) WHERE used_order_id IS NOT NULL;
GO

COMMIT TRANSACTION;
GO

/* ----------------------------------------------------------------------------
   迁移后结构自检（应 = 236 项）。
   ---------------------------------------------------------------------------- */
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

PRINT N'migration.sql：v0.1 → v1.0 迁移完成（+1 列 +15 CHECK +1 筛选唯一索引，指纹 219 → 236）。';
GO
