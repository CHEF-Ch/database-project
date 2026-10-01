/* ============================================================================
   constraint.sql —— 主码 / 候选码 / 外码 / 检查约束（第 4 周产出）
   ----------------------------------------------------------------------------
   前置：必须先跑 sql/00-create.sql + sql/01-schema.sql。本文本给 01-schema.sql
   建出的 17 张表【追加】全部键与检查约束。

   ★ 第 4 周重构（Yang）：把原先写在 01-schema.sql 里的主码/候选码/外码/检查约束
     全部抽到本文本，用 ALTER TABLE 独立创建。最终库结构与第 3 周【一字不差】，
     复现指纹仍为 219 项（110 列 + 17 主码 + 3 唯一约束 + 3 筛选唯一索引 +
     25 外码 + 45 CHECK + 16 DEFAULT）。

   ★ 本文本【不可重复执行】：约束已存在会报"对象已存在 / 约束已存在"。要重跑请
     先重跑 00-create.sql 从空库开始（见 sql/README.md 的复现口径）。

   ★ 键与约束的命名（**延伸了 docs/06/第二周 §五 的规则，需回填文档**）：
     主码 PK_表名        外码 FK_子表_父表        候选码 UQ_表名_字段名
     检查 CK_表名_字段名  默认 DF_表名_字段名
     后两类是本次新增的，理由不是"好看"而是**复现必需**：不显式命名的 DEFAULT /
     CHECK 会由 SQL Server 自动起名（如 DF__item_pro__sale___1A2B3C4D），后缀是
     随机的十六进制，两次复现建出的约束名就不一样，"得到相同的表结构"这条要求
     当场不成立。

   ★ CHECK 的取舍口径（本文本统一按此执行，便于审计）：
     · 列级 CHECK（枚举取值、" > 0 / >= 0" 这类单列取值范围）——写齐。
     · 跨列 CHECK——也写。原写"留给第 4 周"，第 3 周经查证**推翻**：
       docs/06/第二周 §六.8 末段只针对**一条具体约束**（reserved_qty <= stock_qty）
       说"属第 4 周"，并未给出"跨列就推后"的整类规则；第三周任务 §2 明写要"设置
       检查约束"且未区分列级/跨列；第 4 周的 constraint.sql 是"把已写约束抽出来
       演示"，不是"第 4 周才开始写约束"。详见 docs/02 §5.7 第 7 条。

   ★ 创建顺序：① 主码 → ② 外码 → ③ 候选码 → ④ 检查约束 → ⑤ 合法/非法验证 →
     ⑥ 汇总。外码引用主码，故主码必须先建；全部表已由 01-schema 建好，外码之间
     无先后要求。
   ============================================================================ */

SET NOCOUNT ON;

/* 筛选唯一索引（③ 里的 UQ_member_member_phone 等）要求创建时 QUOTED_IDENTIFIER
   与 ANSI_NULLS 均为 ON；sqlcmd 默认 QUOTED_IDENTIFIER = OFF、SSMS 默认 ON，
   故必须显式写死，否则按 sql/README.md 的 sqlcmd 命令会报 Msg 1934。 */
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

/* 每个脚本都是独立一次 sqlcmd 进程，上下文不会从上一个脚本继承。 */
USE [MilkTeaShop];
GO

/* 空表自检：没有表就没有东西可加约束，把 cryptic 的报错换成人话。 */
DECLARE @tbl_count int = (SELECT COUNT(*) FROM sys.tables);
IF @tbl_count = 0
BEGIN
    THROW 51101, N'constraint.sql 要求先执行 sql/01-schema.sql（当前库 0 张表，无表可加约束）。', 1;
END
GO

/* ###########################################################################
   ① 主码（17）
   ########################################################################### */
ALTER TABLE dbo.item_product         ADD CONSTRAINT PK_item_product         PRIMARY KEY (product_id);
ALTER TABLE dbo.item_material        ADD CONSTRAINT PK_item_material        PRIMARY KEY (material_id);
ALTER TABLE dbo.member_member        ADD CONSTRAINT PK_member_member        PRIMARY KEY (member_id);
ALTER TABLE dbo.staff_employee       ADD CONSTRAINT PK_staff_employee       PRIMARY KEY (employee_id);
ALTER TABLE dbo.item_recipe          ADD CONSTRAINT PK_item_recipe          PRIMARY KEY (product_id, material_id);
ALTER TABLE dbo.item_addon_option    ADD CONSTRAINT PK_item_addon_option    PRIMARY KEY (product_id, material_id);
ALTER TABLE dbo.shop_order           ADD CONSTRAINT PK_shop_order           PRIMARY KEY (order_id);
ALTER TABLE dbo.order_item           ADD CONSTRAINT PK_order_item           PRIMARY KEY (order_id, item_line);
ALTER TABLE dbo.inv_restock          ADD CONSTRAINT PK_inv_restock          PRIMARY KEY (restock_id);
ALTER TABLE dbo.order_item_addon     ADD CONSTRAINT PK_order_item_addon     PRIMARY KEY (order_id, item_line, material_id);
ALTER TABLE dbo.order_platform       ADD CONSTRAINT PK_order_platform       PRIMARY KEY (order_id);
ALTER TABLE dbo.inv_restock_item     ADD CONSTRAINT PK_inv_restock_item     PRIMARY KEY (restock_id, material_id);
ALTER TABLE dbo.inv_stock_log        ADD CONSTRAINT PK_inv_stock_log        PRIMARY KEY (stock_log_id);
ALTER TABLE dbo.member_point_log     ADD CONSTRAINT PK_member_point_log     PRIMARY KEY (point_log_id);
ALTER TABLE dbo.member_balance_log   ADD CONSTRAINT PK_member_balance_log   PRIMARY KEY (balance_log_id);
ALTER TABLE dbo.mkt_coupon_rule      ADD CONSTRAINT PK_mkt_coupon_rule      PRIMARY KEY (coupon_rule_id);
ALTER TABLE dbo.mkt_member_coupon    ADD CONSTRAINT PK_mkt_member_coupon    PRIMARY KEY (member_coupon_id);
GO

/* ###########################################################################
   ② 外码（25，其中 3 条带 ON DELETE CASCADE：订单明细 / 加料明细 / 平台单信息）
   ########################################################################### */
ALTER TABLE dbo.item_recipe          ADD CONSTRAINT FK_item_recipe_item_product      FOREIGN KEY (product_id)  REFERENCES dbo.item_product    (product_id);
ALTER TABLE dbo.item_recipe          ADD CONSTRAINT FK_item_recipe_item_material     FOREIGN KEY (material_id) REFERENCES dbo.item_material   (material_id);
ALTER TABLE dbo.item_addon_option    ADD CONSTRAINT FK_item_addon_option_item_product  FOREIGN KEY (product_id)  REFERENCES dbo.item_product  (product_id);
ALTER TABLE dbo.item_addon_option    ADD CONSTRAINT FK_item_addon_option_item_material FOREIGN KEY (material_id) REFERENCES dbo.item_material (material_id);
ALTER TABLE dbo.shop_order           ADD CONSTRAINT FK_shop_order_member_member   FOREIGN KEY (member_id)   REFERENCES dbo.member_member  (member_id);
ALTER TABLE dbo.shop_order           ADD CONSTRAINT FK_shop_order_staff_employee  FOREIGN KEY (employee_id) REFERENCES dbo.staff_employee (employee_id);
ALTER TABLE dbo.order_item           ADD CONSTRAINT FK_order_item_shop_order      FOREIGN KEY (order_id)    REFERENCES dbo.shop_order     (order_id)   ON DELETE CASCADE;
ALTER TABLE dbo.order_item           ADD CONSTRAINT FK_order_item_item_product    FOREIGN KEY (product_id)  REFERENCES dbo.item_product   (product_id);
ALTER TABLE dbo.inv_restock          ADD CONSTRAINT FK_inv_restock_staff_employee FOREIGN KEY (employee_id) REFERENCES dbo.staff_employee  (employee_id);
ALTER TABLE dbo.order_item_addon     ADD CONSTRAINT FK_order_item_addon_order_item    FOREIGN KEY (order_id, item_line) REFERENCES dbo.order_item (order_id, item_line) ON DELETE CASCADE;
ALTER TABLE dbo.order_item_addon     ADD CONSTRAINT FK_order_item_addon_item_material FOREIGN KEY (material_id)        REFERENCES dbo.item_material (material_id);
ALTER TABLE dbo.order_platform       ADD CONSTRAINT FK_order_platform_shop_order   FOREIGN KEY (order_id)    REFERENCES dbo.shop_order    (order_id)   ON DELETE CASCADE;
ALTER TABLE dbo.inv_restock_item     ADD CONSTRAINT FK_inv_restock_item_inv_restock   FOREIGN KEY (restock_id)  REFERENCES dbo.inv_restock   (restock_id);
ALTER TABLE dbo.inv_restock_item     ADD CONSTRAINT FK_inv_restock_item_item_material FOREIGN KEY (material_id) REFERENCES dbo.item_material  (material_id);
ALTER TABLE dbo.inv_stock_log        ADD CONSTRAINT FK_inv_stock_log_item_material  FOREIGN KEY (material_id) REFERENCES dbo.item_material  (material_id);
ALTER TABLE dbo.inv_stock_log        ADD CONSTRAINT FK_inv_stock_log_shop_order     FOREIGN KEY (order_id)    REFERENCES dbo.shop_order    (order_id);
ALTER TABLE dbo.inv_stock_log        ADD CONSTRAINT FK_inv_stock_log_inv_restock    FOREIGN KEY (restock_id)  REFERENCES dbo.inv_restock   (restock_id);
ALTER TABLE dbo.inv_stock_log        ADD CONSTRAINT FK_inv_stock_log_staff_employee FOREIGN KEY (employee_id) REFERENCES dbo.staff_employee (employee_id);
ALTER TABLE dbo.member_point_log     ADD CONSTRAINT FK_member_point_log_member_member FOREIGN KEY (member_id) REFERENCES dbo.member_member (member_id);
ALTER TABLE dbo.member_point_log     ADD CONSTRAINT FK_member_point_log_shop_order    FOREIGN KEY (order_id)  REFERENCES dbo.shop_order    (order_id);
ALTER TABLE dbo.member_balance_log   ADD CONSTRAINT FK_member_balance_log_member_member FOREIGN KEY (member_id) REFERENCES dbo.member_member (member_id);
ALTER TABLE dbo.member_balance_log   ADD CONSTRAINT FK_member_balance_log_shop_order    FOREIGN KEY (order_id)  REFERENCES dbo.shop_order    (order_id);
ALTER TABLE dbo.mkt_member_coupon    ADD CONSTRAINT FK_mkt_member_coupon_mkt_coupon_rule FOREIGN KEY (coupon_rule_id) REFERENCES dbo.mkt_coupon_rule (coupon_rule_id);
ALTER TABLE dbo.mkt_member_coupon    ADD CONSTRAINT FK_mkt_member_coupon_member_member   FOREIGN KEY (member_id)      REFERENCES dbo.member_member  (member_id);
ALTER TABLE dbo.mkt_member_coupon    ADD CONSTRAINT FK_mkt_member_coupon_shop_order      FOREIGN KEY (used_order_id)  REFERENCES dbo.shop_order     (order_id);
GO

/* ###########################################################################
   ③ 候选码（3 条 UNIQUE 约束 + 3 条筛选唯一索引）
   ---------------------------------------------------------------------------
   可空唯一列（phone / pay_txn_no / external_ref）不能直接用 UNIQUE 约束——
   SQL Server 的 UNIQUE 只允许一个 NULL，与 docs/02 §5.2"可空，多 NULL 不冲突"
   矛盾，故改用带筛选条件的唯一索引 WHERE 列 IS NOT NULL。详见 docs/02 §5.7 #4。
   ########################################################################### */
ALTER TABLE dbo.item_product      ADD CONSTRAINT UQ_item_product_name_size       UNIQUE (name, size);
ALTER TABLE dbo.item_material     ADD CONSTRAINT UQ_item_material_name           UNIQUE (name);
ALTER TABLE dbo.order_platform    ADD CONSTRAINT UQ_order_platform_platform_order_no UNIQUE (platform_order_no);
GO

CREATE UNIQUE INDEX UQ_member_member_phone
    ON dbo.member_member (phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX UQ_shop_order_pay_txn_no
    ON dbo.shop_order (pay_txn_no) WHERE pay_txn_no IS NOT NULL;
CREATE UNIQUE INDEX UQ_member_balance_log_external_ref
    ON dbo.member_balance_log (external_ref) WHERE external_ref IS NOT NULL;
GO

/* ###########################################################################
   ④ 检查约束（45）
   ########################################################################### */
-- item_product
ALTER TABLE dbo.item_product ADD CONSTRAINT CK_item_product_size        CHECK (size IN ('SMALL', 'MEDIUM', 'LARGE'));
ALTER TABLE dbo.item_product ADD CONSTRAINT CK_item_product_sale_status CHECK (sale_status IN ('ON_SALE', 'OFF_SHELF'));

-- item_material
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_material_kind   CHECK (material_kind IN ('INGREDIENT', 'ADDON'));
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_unit            CHECK (unit IN (N'克', N'毫升', N'个'));
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_stock_qty       CHECK (stock_qty    >= 0);
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_reserved_qty    CHECK (reserved_qty >= 0);
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_safety_stock    CHECK (safety_stock >= 0);
-- "能放 0 天"无意义，故 > 0。列为 NULL 时 CHECK 求值为 UNKNOWN，不拦——
-- 这正是"NULL = 不管控效期"想要的行为，无需额外写 IS NULL OR ...。
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_shelf_life_days CHECK (shelf_life_days > 0);
-- 预留量不得超过现存量（docs/02 §5.7 第 7 条，第 3 周已定为本周落地）
ALTER TABLE dbo.item_material ADD CONSTRAINT CK_item_material_reserved_le_stock CHECK (reserved_qty <= stock_qty);

-- member_member
ALTER TABLE dbo.member_member ADD CONSTRAINT CK_member_member_points  CHECK (points  >= 0);
ALTER TABLE dbo.member_member ADD CONSTRAINT CK_member_member_balance CHECK (balance >= 0);

-- staff_employee
ALTER TABLE dbo.staff_employee ADD CONSTRAINT CK_staff_employee_job_title     CHECK (job_title     IN ('CASHIER', 'MAKER', 'STOCK_KEEPER', 'MANAGER'));
ALTER TABLE dbo.staff_employee ADD CONSTRAINT CK_staff_employee_employ_status CHECK (employ_status IN ('ACTIVE', 'RESIGNED'));

-- item_recipe（事件型：用量 0 表示"发生了 0 次"，语义自相矛盾）
ALTER TABLE dbo.item_recipe ADD CONSTRAINT CK_item_recipe_qty CHECK (qty > 0);

-- item_addon_option
ALTER TABLE dbo.item_addon_option ADD CONSTRAINT CK_item_addon_option_addon_price  CHECK (addon_price > 0);
ALTER TABLE dbo.item_addon_option ADD CONSTRAINT CK_item_addon_option_addon_qty    CHECK (addon_qty   > 0);
ALTER TABLE dbo.item_addon_option ADD CONSTRAINT CK_item_addon_option_addon_status CHECK (addon_status IN ('AVAILABLE', 'UNAVAILABLE'));

-- shop_order（order_mode / pay_method 可空：NULL 求值为 UNKNOWN，CHECK 不拦，
-- 正好表达"平台单无 order_mode""未付款无 pay_method"）
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_channel      CHECK (channel      IN ('COUNTER', 'PLATFORM'));
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_order_mode   CHECK (order_mode   IN ('COUNTER_CASHIER', 'COUNTER_SELF'));
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_order_status CHECK (order_status IN ('PENDING', 'PAID', 'MAKING', 'COMPLETED', 'CANCELLED', 'REFUNDED'));
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_pay_method   CHECK (pay_method   IN ('CASH', 'ONLINE', 'BALANCE'));
-- 实付金额不得为负。券后金额 = item_subtotal − coupon_discount，
-- 本约束即"券优惠不得超过券前金额"的结果表达（docs/02 §5.7 第 7 条）。
ALTER TABLE dbo.shop_order ADD CONSTRAINT CK_shop_order_total_amount CHECK (total_amount >= 0);

-- order_item（事件型：杯数必须 > 0）
ALTER TABLE dbo.order_item ADD CONSTRAINT CK_order_item_size        CHECK (size        IN ('SMALL', 'MEDIUM', 'LARGE'));
ALTER TABLE dbo.order_item ADD CONSTRAINT CK_order_item_sugar_level CHECK (sugar_level IN ('FULL', 'SEVENTY', 'HALF', 'THIRTY', 'NONE'));
ALTER TABLE dbo.order_item ADD CONSTRAINT CK_order_item_ice_level   CHECK (ice_level   IN ('REGULAR', 'LESS', 'NONE'));
ALTER TABLE dbo.order_item ADD CONSTRAINT CK_order_item_temp_level  CHECK (temp_level  IN ('HOT', 'COLD'));
ALTER TABLE dbo.order_item ADD CONSTRAINT CK_order_item_qty         CHECK (qty > 0);

-- inv_restock（到货时间与状态互为充要：RECEIVED 必有 received_at，
-- PENDING/CANCELLED 必无。用 OR 展开双向蕴含——T-SQL 无布尔类型，谓词不能作
-- `=` 操作数，实测报错 102；`= NULL` 则得 UNKNOWN 被 CHECK 放行而静默失效）
ALTER TABLE dbo.inv_restock ADD CONSTRAINT CK_inv_restock_restock_status CHECK (restock_status IN ('PENDING', 'RECEIVED', 'CANCELLED'));
ALTER TABLE dbo.inv_restock ADD CONSTRAINT CK_inv_restock_received CHECK (
    (received_at IS NULL     AND restock_status <> 'RECEIVED')
 OR (received_at IS NOT NULL AND restock_status  = 'RECEIVED')
);

-- order_item_addon（加料数量必须 > 0：0 会在统计中虚增"加料被点次数"）
ALTER TABLE dbo.order_item_addon ADD CONSTRAINT CK_order_item_addon_qty CHECK (qty > 0);

-- order_platform（配送费不可能为负：0 = 免配送费，是合法状态）
ALTER TABLE dbo.order_platform ADD CONSTRAINT CK_order_platform_platform_name CHECK (platform_name IN ('MEITUAN', 'ELEME'));
ALTER TABLE dbo.order_platform ADD CONSTRAINT CK_order_platform_delivery_fee  CHECK (delivery_fee >= 0);

-- inv_restock_item
ALTER TABLE dbo.inv_restock_item ADD CONSTRAINT CK_inv_restock_item_qty CHECK (qty > 0);

-- inv_stock_log（流水数值存正数绝对值，方向由 type 枚举决定，docs/02 §5.4）
ALTER TABLE dbo.inv_stock_log ADD CONSTRAINT CK_inv_stock_log_log_type CHECK (log_type IN ('IN', 'SALE', 'LOSS', 'GAIN', 'SHORT'));
ALTER TABLE dbo.inv_stock_log ADD CONSTRAINT CK_inv_stock_log_qty      CHECK (qty > 0);

-- member_point_log（order_id 与 point_type 互为充要：EARN_RECHARGE 无订单，其余必有）
ALTER TABLE dbo.member_point_log ADD CONSTRAINT CK_member_point_log_point_type CHECK (point_type IN ('EARN_SALE', 'EARN_RECHARGE', 'REVERSAL'));
ALTER TABLE dbo.member_point_log ADD CONSTRAINT CK_member_point_log_points     CHECK (points > 0);
ALTER TABLE dbo.member_point_log ADD CONSTRAINT CK_member_point_log_order CHECK (
    (point_type  = 'EARN_RECHARGE' AND order_id IS NULL)
 OR (point_type <> 'EARN_RECHARGE' AND order_id IS NOT NULL)
);

-- member_balance_log
ALTER TABLE dbo.member_balance_log ADD CONSTRAINT CK_member_balance_log_balance_type CHECK (balance_type IN ('RECHARGE', 'SPEND', 'REFUND'));
ALTER TABLE dbo.member_balance_log ADD CONSTRAINT CK_member_balance_log_amount       CHECK (amount > 0);

-- mkt_coupon_rule（门槛必须为正：不允许无门槛券；满减额不得低于门槛，允许"满 X 减 X"免单券）
ALTER TABLE dbo.mkt_coupon_rule ADD CONSTRAINT CK_mkt_coupon_rule_coupon_type CHECK (coupon_type IN ('FULL_REDUCTION'));
ALTER TABLE dbo.mkt_coupon_rule ADD CONSTRAINT CK_mkt_coupon_rule_threshold  CHECK (threshold_amount > 0);
ALTER TABLE dbo.mkt_coupon_rule ADD CONSTRAINT CK_mkt_coupon_rule_discount   CHECK (discount_amount > 0 AND discount_amount <= threshold_amount);
ALTER TABLE dbo.mkt_coupon_rule ADD CONSTRAINT CK_mkt_coupon_rule_valid      CHECK (valid_to >= valid_from);

-- mkt_member_coupon
ALTER TABLE dbo.mkt_member_coupon ADD CONSTRAINT CK_mkt_member_coupon_coupon_status CHECK (coupon_status IN ('UNUSED', 'LOCKED', 'USED', 'RETURNED'));
GO

/* ###########################################################################
   ⑤ 合法 / 非法验证用例
   ---------------------------------------------------------------------------
   每条用例在事务里跑、最后回滚，故不留下任何数据（本文本在 02-seed 之前执行，
   库仍为空）。结果先存进局部变量，回滚后再写进 #results——否则回滚会把结果行
   一起冲掉。"应被拒"的用例期望错误号：547（CHECK/外码）、2627（主码/唯一约束）、
   2601（筛选唯一索引）。
   ########################################################################### */
CREATE TABLE #results (
    test_id     nvarchar(10)  NOT NULL,
    description nvarchar(250) NOT NULL,
    passed      bit           NOT NULL,
    detail      nvarchar(100) NULL
);
GO

-- T01 主码重复
DECLARE @e1 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.item_product (product_id, name, size, unit_price) VALUES ('P9999999', N'测试商品', 'MEDIUM', 1.00);
    BEGIN TRY
        INSERT INTO dbo.item_product (product_id, name, size, unit_price) VALUES ('P9999999', N'重复主码', 'MEDIUM', 1.00);
    END TRY BEGIN CATCH
        SET @e1 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T01', N'PK_item_product：主码重复应被拒（期望 2627）', CASE WHEN @e1 = 2627 THEN 1 ELSE 0 END, CAST(@e1 AS nvarchar(20)));
GO

-- T02 唯一约束重复
DECLARE @e2 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.item_material (material_id, name, unit, material_kind) VALUES ('M9999999', N'测试原料', N'克', 'INGREDIENT');
    BEGIN TRY
        INSERT INTO dbo.item_material (material_id, name, unit, material_kind) VALUES ('M9999998', N'测试原料', N'克', 'INGREDIENT');
    END TRY BEGIN CATCH
        SET @e2 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T02', N'UQ_item_material_name：原料名重复应被拒（期望 2627）', CASE WHEN @e2 = 2627 THEN 1 ELSE 0 END, CAST(@e2 AS nvarchar(20)));
GO

-- T03 复合主码重复
DECLARE @e3 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.item_product  (product_id, name, size, unit_price) VALUES ('P9999998', N'测试商品2', 'MEDIUM', 1.00);
    INSERT INTO dbo.item_material (material_id, name, unit, material_kind) VALUES ('M9999998', N'测试原料2', N'克', 'INGREDIENT');
    INSERT INTO dbo.item_recipe  (product_id, material_id, qty) VALUES ('P9999998', 'M9999998', 1.00);
    BEGIN TRY
        INSERT INTO dbo.item_recipe (product_id, material_id, qty) VALUES ('P9999998', 'M9999998', 2.00);
    END TRY BEGIN CATCH
        SET @e3 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T03', N'PK_item_recipe：复合主码重复应被拒（期望 2627）', CASE WHEN @e3 = 2627 THEN 1 ELSE 0 END, CAST(@e3 AS nvarchar(20)));
GO

-- T04 筛选唯一索引：多个 NULL 手机号应放行
DECLARE @ok4 bit = 1, @e4 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.member_member (member_id, phone, name, created_at) VALUES ('C9999991', NULL, N'甲', '2026-09-16 10:00:00');
        INSERT INTO dbo.member_member (member_id, phone, name, created_at) VALUES ('C9999992', NULL, N'乙', '2026-09-16 10:00:00');
    END TRY BEGIN CATCH
        SET @ok4 = 0; SET @e4 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T04', N'UQ_member_member_phone：两个 NULL 手机号应都放行', @ok4, CAST(@e4 AS nvarchar(20)));
GO

-- T05 筛选唯一索引：非空手机号重复应被拒
DECLARE @e5 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.member_member (member_id, phone, name, created_at) VALUES ('C9999993', '13900000001', N'丙', '2026-09-16 10:00:00');
    BEGIN TRY
        INSERT INTO dbo.member_member (member_id, phone, name, created_at) VALUES ('C9999994', '13900000001', N'丁', '2026-09-16 10:00:00');
    END TRY BEGIN CATCH
        SET @e5 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T05', N'UQ_member_member_phone：非空手机号重复应被拒（期望 2601）', CASE WHEN @e5 = 2601 THEN 1 ELSE 0 END, CAST(@e5 AS nvarchar(20)));
GO

-- T06 外码：不存在的父行应被拒
DECLARE @e6 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.order_item (order_id, item_line, product_id, size, qty, unit_price)
        VALUES ('202609999999', 1, 'P9999999', 'MEDIUM', 1, 1.00);
    END TRY BEGIN CATCH
        SET @e6 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T06', N'FK_order_item：不存在的 order_id/product_id 应被拒（期望 547）', CASE WHEN @e6 = 547 THEN 1 ELSE 0 END, CAST(@e6 AS nvarchar(20)));
GO

-- T07 枚举检查：非法杯型
DECLARE @e7 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.item_product (product_id, name, size, unit_price) VALUES ('P9999997', N'坏杯型', 'HUGE', 1.00);
    END TRY BEGIN CATCH
        SET @e7 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T07', N'CK_item_product_size：非法杯型应被拒（期望 547）', CASE WHEN @e7 = 547 THEN 1 ELSE 0 END, CAST(@e7 AS nvarchar(20)));
GO

-- T08 枚举检查：非法职务
DECLARE @e8 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.staff_employee (employee_id, name, job_title) VALUES ('E9999999', N'测试', 'CEO');
    END TRY BEGIN CATCH
        SET @e8 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T08', N'CK_staff_employee_job_title：非法职务应被拒（期望 547）', CASE WHEN @e8 = 547 THEN 1 ELSE 0 END, CAST(@e8 AS nvarchar(20)));
GO

-- T09 取值范围检查：负库存
DECLARE @e9 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.item_material (material_id, name, unit, material_kind, stock_qty)
        VALUES ('M9999999', N'坏库存', N'克', 'INGREDIENT', -1);
    END TRY BEGIN CATCH
        SET @e9 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T09', N'CK_item_material_stock_qty：负库存应被拒（期望 547）', CASE WHEN @e9 = 547 THEN 1 ELSE 0 END, CAST(@e9 AS nvarchar(20)));
GO

-- T10 取值范围检查：券有效期止早于起
DECLARE @e10 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.mkt_coupon_rule (coupon_rule_id, coupon_name, coupon_type, threshold_amount, discount_amount, valid_from, valid_to)
        VALUES ('CP999999', N'坏券', 'FULL_REDUCTION', 30.00, 5.00, '2026-10-01', '2026-09-01');
    END TRY BEGIN CATCH
        SET @e10 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T10', N'CK_mkt_coupon_rule_valid：有效期止早于起应被拒（期望 547）', CASE WHEN @e10 = 547 THEN 1 ELSE 0 END, CAST(@e10 AS nvarchar(20)));
GO

-- T11 跨列检查：预留量 > 现存量
DECLARE @e11 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.item_material (material_id, name, unit, material_kind, stock_qty, reserved_qty)
        VALUES ('M9999998', N'预留超标', N'克', 'INGREDIENT', 5, 10);
    END TRY BEGIN CATCH
        SET @e11 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T11', N'CK_item_material_reserved_le_stock：预留量>现存量应被拒（期望 547）', CASE WHEN @e11 = 547 THEN 1 ELSE 0 END, CAST(@e11 AS nvarchar(20)));
GO

-- T12 跨列检查：补货状态与到货时间不匹配
DECLARE @e12 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.staff_employee (employee_id, name, job_title) VALUES ('E9999998', N'测试库管', 'STOCK_KEEPER');
    BEGIN TRY
        INSERT INTO dbo.inv_restock (restock_id, supplier_name, restock_status, employee_id, created_at, received_at)
        VALUES ('R9999999', N'供应商X', 'RECEIVED', 'E9999998', '2026-09-16 10:00:00', NULL);
    END TRY BEGIN CATCH
        SET @e12 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T12', N'CK_inv_restock_received：RECEIVED 却 received_at 为 NULL 应被拒（期望 547）', CASE WHEN @e12 = 547 THEN 1 ELSE 0 END, CAST(@e12 AS nvarchar(20)));
GO

-- T13 跨列检查：消费计分却无订单
DECLARE @e13 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.member_member (member_id, name, created_at) VALUES ('C9999999', N'测试会员', '2026-09-16 10:00:00');
    BEGIN TRY
        INSERT INTO dbo.member_point_log (member_id, point_type, points, order_id, created_at)
        VALUES ('C9999999', 'EARN_SALE', 10, NULL, '2026-09-16 10:00:00');
    END TRY BEGIN CATCH
        SET @e13 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T13', N'CK_member_point_log_order：消费计分却无订单应被拒（期望 547）', CASE WHEN @e13 = 547 THEN 1 ELSE 0 END, CAST(@e13 AS nvarchar(20)));
GO

-- T14 合法插入：商品
DECLARE @ok14 bit = 1, @e14 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.item_product (product_id, name, size, unit_price) VALUES ('P9999996', N'合法商品', 'LARGE', 2.00);
    END TRY BEGIN CATCH
        SET @ok14 = 0; SET @e14 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T14', N'合法商品插入应成功', @ok14, CAST(@e14 AS nvarchar(20)));
GO

-- T15 合法边界：满 X 减 X（免单券）
DECLARE @ok15 bit = 1, @e15 int = 0;
BEGIN TRAN;
    BEGIN TRY
        INSERT INTO dbo.mkt_coupon_rule (coupon_rule_id, coupon_name, coupon_type, threshold_amount, discount_amount, valid_from, valid_to)
        VALUES ('CP999998', N'免单券', 'FULL_REDUCTION', 20.00, 20.00, '2026-09-01', '2026-10-31');
    END TRY BEGIN CATCH
        SET @ok15 = 0; SET @e15 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T15', N'CK_mkt_coupon_rule_discount：满 X 减 X（免单券）边界应放行', @ok15, CAST(@e15 AS nvarchar(20)));
GO

-- T16 合法跨列：PENDING 且 received_at 为 NULL
DECLARE @ok16 bit = 1, @e16 int = 0;
BEGIN TRAN;
    INSERT INTO dbo.staff_employee (employee_id, name, job_title) VALUES ('E9999997', N'测试库管2', 'STOCK_KEEPER');
    BEGIN TRY
        INSERT INTO dbo.inv_restock (restock_id, supplier_name, restock_status, employee_id, created_at, received_at)
        VALUES ('R9999998', N'供应商Y', 'PENDING', 'E9999997', '2026-09-16 10:00:00', NULL);
    END TRY BEGIN CATCH
        SET @ok16 = 0; SET @e16 = ERROR_NUMBER();
    END CATCH;
ROLLBACK TRAN;
INSERT INTO #results VALUES (N'T16', N'CK_inv_restock_received：PENDING + NULL received_at 应放行', @ok16, CAST(@e16 AS nvarchar(20)));
GO

/* 测试结果汇总 */
SELECT
      COUNT(*)                                               AS total_tests
    , SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END)            AS passed_count
    , SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END)            AS failed_count
    , CASE WHEN SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) = 0
             THEN N'ALL TESTS PASSED' ELSE N'HAS FAILURES' END AS result
FROM #results;

SELECT test_id, description, passed, detail
FROM #results
ORDER BY test_id;

DROP TABLE #results;
GO

/* ###########################################################################
   ⑥ 约束汇总（结构指纹，应 = 第 3 周的 219 项）
   ########################################################################### */
SELECT
      t.name AS table_name
    , (SELECT COUNT(*) FROM sys.key_constraints   k  WHERE k.parent_object_id  = t.object_id AND k.type = 'PK') AS pk_count
    , (SELECT COUNT(*) FROM sys.foreign_keys      f  WHERE f.parent_object_id  = t.object_id) AS fk_count
    , (SELECT COUNT(*) FROM sys.check_constraints cc WHERE cc.parent_object_id = t.object_id) AS check_count
    , (  (SELECT COUNT(*) FROM sys.key_constraints u WHERE u.parent_object_id = t.object_id AND u.type = 'UQ')
       + (SELECT COUNT(*) FROM sys.indexes i WHERE i.object_id = t.object_id
              AND i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0)) AS unique_count
FROM sys.tables t
ORDER BY t.name;

-- 汇总：110 列（01-schema）+ 17 主码 + 3 唯一约束 + 3 筛选唯一索引 + 25 外码
--       + 45 CHECK + 16 DEFAULT（01-schema）= 219 项
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

PRINT N'constraint.sql: 17 主码 + 25 外码 + 6 候选码 + 45 CHECK 已全部创建（连同 01-schema 的 110 列 + 16 DEFAULT，指纹 = 219 项）。';
PRINT N'constraint.sql: 下一步执行 sql/02-seed.sql（装载样例数据）。';
GO

/* ===========================================================================
   附：本脚本【故意没写】的 2 项，以及原因（避免被当成漏写）
   ---------------------------------------------------------------------------
   1. CHECK (unit_price > 0) 之类"文档没列、但显然该有"的约束
      docs/02 §2.2 给 unit_price 的约束只写了"非空"，§1.1 与第二周 §六.8 也未列
      售价。docs/06/第三周 §三.2 要求"脚本与文档必须一致，不能两个版本并存"，
      故不擅自加。同类还有：commission_amount >= 0、valid_from/valid_to 的其他
      关系等——都要先回填 docs/02 再改脚本。

      ★ 本项的处理方式已确立，且第 3 周已按此走了一轮：**先回填 docs/02，再改脚本**。
        第 3 周据此回填了 6 条（total_amount >= 0、delivery_fee >= 0、
        threshold_amount > 0、discount_amount <= threshold_amount、
        valid_to >= valid_from、unit 类型与取值），并同步改脚本——
        记录见 docs/02 §5.7 第 6、7 条。其余同类项仍按"先回填再改"执行。

   2. item_addon_option.material_id "须可作加料"（docs/02 §2.2-4 括注）
      跨表规则（要读 item_material.material_kind），CHECK 表达不了，需触发器或
      标量 UDF。按 docs/06/第三周 §三.3"记录下来并回填文档"处理，此处不写。
      同类还有：shop_order 上"channel=PLATFORM 时 order_mode 必为 NULL"、
      "order_status=PENDING 时 pay_method 必为 NULL"、以及"券折扣不得超过
      mkt_coupon_rule 登记的门槛"等业务规则——均需跨表读取，CHECK 无法表达。

   另记 1 处文档待修：
   · docs/02 §1.1 第 8 项只列了 order_item.size 的杯型取值，而 item_product.size
     用的是同一域（§2.2-1 写作"杯型 SMALL/MEDIUM/LARGE"），本脚本按同域加了
     CK_item_product_size。请把 §1.1 #8 补成两处同域（参照 #9 把三个字段并成
     一行的写法），否则文档与脚本又对不上了。

   （原第 1 项"CHECK (reserved_qty <= stock_qty) 留给第 4 周"已于第 3 周**改为本周写入**，
     理由见本脚本头部"CHECK 的取舍口径"及 docs/02 §5.7 第 7 条。
     原"另记"里"docs/06 第三周 §二 写 24 条"一条已过期——§二 早已改为 25 条，故删除。）
   =========================================================================== */
