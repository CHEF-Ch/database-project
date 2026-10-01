/* ============================================================================
   01-schema.sql —— 建表（17 张，只建「列 + 非空 + 默认值」）
   ----------------------------------------------------------------------------
   前置：必须先跑 sql/00-create.sql（本脚本【要求空库】）。
   依据：docs/02-数据字典.md §2.2 的 17 张表字段定义，逐字段对齐。

   ★ 第 4 周重构（Yang）：主码 / 候选码 / 外码 / 检查约束【不再】写在本文本里，
     而是全部抽到独立的 sql/constraint.sql（第 4 周产出）。本文本只保留：
        · 110 列 + 每列的类型/长度/精度/可空
        · 16 条 DEFAULT（DF_表名_字段名）
     两者合并后与第 3 周的库结构【一字不差】——复现指纹仍为 219 项
     （110 列 + 17 主码 + 3 唯一约束 + 3 筛选唯一索引 + 25 外码 + 45 CHECK + 16 DEFAULT）。
     键与约束的命名规则、CHECK 取舍口径、以及"故意没写的 2 项"说明，均见 constraint.sql。

   ★ 建表顺序仍沿用 docs/06-进度/第三周任务流程与状态.md §二 的外码拓扑序
     （批次 1—10）：本文本虽已不含外码，但顺序与 constraint.sql / 02-seed.sql 的
     依赖顺序一致，保留便于审计，且换环境复现时行为不变。

   ★ 本脚本【不可重复执行】：表已存在会报"对象已存在"。要重跑请先重跑
     00-create.sql 把库清空——这正是本目录的复现口径（见 sql/README.md）。
   ============================================================================ */

SET NOCOUNT ON;

/* ---------------------------------------------------------------------------
   沿用全目录约定，把两条 SET 显式写死、不依赖客户端默认值。筛选唯一索引虽已
   移至 constraint.sql，但这里保持一致（sqlcmd 默认 QUOTED_IDENTIFIER = OFF，
   SSMS 默认 ON，不同工具要得到同样结果就不能依赖默认值）。
   --------------------------------------------------------------------------- */
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

/* 每个脚本都是独立一次 sqlcmd 进程，上下文不会从上一个脚本继承，
   故本脚本必须自己切库（库名是全组约定，见 00-create.sql 头部说明）。 */
USE [MilkTeaShop];
GO

/* ---------------------------------------------------------------------------
   空库自检：把 cryptic 的报错换成人话。
   没有这一段，误跑第二次会得到 "There is already an object named
   'item_product' in the database."，看的人还得自己猜到要重跑 00-create.sql。
   本脚本定义了本库的全部 17 张表，故"空库"= 一张表都没有。
   --------------------------------------------------------------------------- */
DECLARE @tbl_count int = (SELECT COUNT(*) FROM sys.tables);
IF @tbl_count > 0
BEGIN
    DECLARE @msg nvarchar(400) =
        N'01-schema.sql 要求空库，但当前库已有 ' + CAST(@tbl_count AS nvarchar(10)) +
        N' 张表（本脚本定义本库全部 17 张表，重复执行必冲突）。请先重跑 sql/00-create.sql 删库重建，再执行本脚本。';
    THROW 51100, @msg, 1;
END
GO

/* ###########################################################################
   批次 1：四张主数据表（无外码，可任意顺序）
   ########################################################################### */

/* ===========================================================================
   1. item_product 商品（一行 = 一款可售饮品）           docs/02 §2.2-1
   =========================================================================== */
CREATE TABLE dbo.item_product
(
    product_id   CHAR(8)       NOT NULL,   -- 主码，P + 7 位序号
    name         NVARCHAR(100) NOT NULL,   -- 商品名（不含杯型）
    size         VARCHAR(10)   NOT NULL,   -- 杯型 SMALL/MEDIUM/LARGE
    unit_price   DECIMAL(10,2) NOT NULL,   -- 售价
    sale_status  VARCHAR(10)   NOT NULL
        CONSTRAINT DF_item_product_sale_status DEFAULT ('ON_SALE')
);
GO

/* ===========================================================================
   2. item_material 原料/加料（一行 = 一种原料）         docs/02 §2.2-2
      原料与加料是"同一实体的两种角色"，故合并一张表、用 material_kind 区分。
   =========================================================================== */
CREATE TABLE dbo.item_material
(
    material_id     CHAR(8)       NOT NULL,  -- 主码，M + 7 位序号
    name            NVARCHAR(100) NOT NULL,  -- 原料名
    -- unit 用 NVARCHAR 而非 VARCHAR：seed 存的取值是中文（N'克'/N'毫升'/N'个'）。
    -- VARCHAR 存中文受库排序规则影响，可能乱码（docs/02 §5.7 第 6 条）。
    unit            NVARCHAR(10)  NOT NULL,  -- 单位（克/毫升/个），只在原料表存
    material_kind   VARCHAR(10)   NOT NULL,  -- INGREDIENT 原料 / ADDON 加料
    stock_qty       DECIMAL(10,2) NOT NULL CONSTRAINT DF_item_material_stock_qty    DEFAULT (0),
    reserved_qty    DECIMAL(10,2) NOT NULL CONSTRAINT DF_item_material_reserved_qty DEFAULT (0),
    safety_stock    DECIMAL(10,2) NOT NULL CONSTRAINT DF_item_material_safety_stock DEFAULT (0),
    shelf_life_days INT           NULL       -- 保质期天数；NULL = 不管控效期
);
GO

/* ===========================================================================
   3. member_member 会员（一行 = 一位会员）              docs/02 §2.2-12
      积分/余额存快照列，同时另写流水表（docs/02 §5.3 快照+流水双写）。
   =========================================================================== */
CREATE TABLE dbo.member_member
(
    member_id  CHAR(8)       NOT NULL,   -- 主码，C + 7 位序号
    phone      VARCHAR(20)   NULL,       -- 手机号；微信未绑手机可空
    name       NVARCHAR(100) NULL,       -- 姓名/昵称
    points     INT           NOT NULL CONSTRAINT DF_member_member_points  DEFAULT (0),
    balance    DECIMAL(10,2) NOT NULL CONSTRAINT DF_member_member_balance DEFAULT (0),
    created_at DATETIME2(0)  NOT NULL    -- 入会时间
);
GO

/* ===========================================================================
   4. staff_employee 员工（一行 = 一名员工）             docs/02 §2.2-17
   =========================================================================== */
CREATE TABLE dbo.staff_employee
(
    employee_id   CHAR(8)       NOT NULL,  -- 主码，E + 7 位序号
    name          NVARCHAR(100) NOT NULL,  -- 姓名
    job_title     VARCHAR(20)   NOT NULL,  -- 职务
    employ_status VARCHAR(10)   NOT NULL
        CONSTRAINT DF_staff_employee_employ_status DEFAULT ('ACTIVE')
);
GO

/* ###########################################################################
   批次 2：商品域的关系表（只引用批次 1）
   ########################################################################### */

/* ===========================================================================
   5. item_recipe 配方（一行 = 某商品对某原料的标准用量）  docs/02 §2.2-3
      商品×原料的多对多中间表，关系属性是"用量"。
   =========================================================================== */
CREATE TABLE dbo.item_recipe
(
    product_id  CHAR(8)       NOT NULL,
    material_id CHAR(8)       NOT NULL,
    qty         DECIMAL(10,2) NOT NULL    -- 每杯标准用量（单位继承原料）
);
GO

/* ===========================================================================
   6. item_addon_option 商品-加料选项（一行 = 某商品的一种可选加料）
                                                          docs/02 §2.2-4
      加料单价放这里、原料表不设价格列：单价函数依赖于 (product_id, material_id)，
      不依赖于 material_id 单独一边（docs/02 §2.1 字段级决定 1）。
   =========================================================================== */
CREATE TABLE dbo.item_addon_option
(
    product_id   CHAR(8)       NOT NULL,
    material_id  CHAR(8)       NOT NULL,
    addon_price  DECIMAL(10,2) NOT NULL,   -- 每份加料单价
    addon_qty    DECIMAL(10,2) NOT NULL,   -- 每份加料标准用量
    addon_status VARCHAR(20)   NOT NULL    -- UNAVAILABLE 长 11，10→20 见 docs/02 §5.7 #3
        CONSTRAINT DF_item_addon_option_addon_status DEFAULT ('AVAILABLE')
);
GO

/* ###########################################################################
   批次 3：订单单头（引用批次 1 的会员与员工）
   ########################################################################### */

/* ===========================================================================
   7. shop_order 订单（一行 = 一次交易）                 docs/02 §2.2-5
   =========================================================================== */
CREATE TABLE dbo.shop_order
(
    order_id        CHAR(12)      NOT NULL,  -- 主码，YYYYMMDD + 4 位当日序号
    channel         VARCHAR(10)   NOT NULL,  -- COUNTER 到店 / PLATFORM 外卖平台
    order_mode      VARCHAR(20)   NULL,      -- 仅到店单有值；平台单为 NULL
    order_status    VARCHAR(20)   NOT NULL,  -- 6 状态，见 docs/01 §4.6
    member_id       CHAR(8)       NULL,      -- 非会员单/平台单为 NULL
    employee_id     CHAR(8)       NOT NULL,  -- 经办；自助单记收银/制作员工
    item_subtotal   DECIMAL(10,2) NOT NULL,  -- 券前金额（商品 + 加料）
    coupon_discount DECIMAL(10,2) NOT NULL CONSTRAINT DF_shop_order_coupon_discount DEFAULT (0),
    total_amount    DECIMAL(10,2) NOT NULL,  -- = item_subtotal − coupon_discount
    pay_method      VARCHAR(20)   NULL,      -- 未付款为 NULL
    pay_txn_no      VARCHAR(64)   NULL,      -- 线上支付流水号（幂等键）
    created_at      DATETIME2(0)  NOT NULL,  -- 下单时间
    paid_at         DATETIME2(0)  NULL,
    completed_at    DATETIME2(0)  NULL,
    refund_amount   DECIMAL(10,2) NULL,      -- 仅 REFUNDED 单有值
    refund_at       DATETIME2(0)  NULL
);
GO

/* ###########################################################################
   批次 4：订单明细（引用批次 3 的订单、批次 1 的商品）
   ########################################################################### */

/* ===========================================================================
   8. order_item 订单明细（一行 = 一款饮品及其杯数）      docs/02 §2.2-6
   =========================================================================== */
CREATE TABLE dbo.order_item
(
    order_id    CHAR(12)      NOT NULL,
    item_line   SMALLINT      NOT NULL,   -- 行号，单内自增
    product_id  CHAR(8)       NOT NULL,
    size        VARCHAR(10)   NOT NULL,   -- 杯型快照（商品改名/改杯型不影响历史）
    sugar_level VARCHAR(10)   NOT NULL CONSTRAINT DF_order_item_sugar_level DEFAULT ('FULL'),
    ice_level   VARCHAR(10)   NOT NULL CONSTRAINT DF_order_item_ice_level   DEFAULT ('REGULAR'),
    temp_level  VARCHAR(10)   NOT NULL CONSTRAINT DF_order_item_temp_level  DEFAULT ('COLD'),
    qty         INT           NOT NULL,   -- 杯数（同款同定制合并）
    unit_price  DECIMAL(10,2) NOT NULL    -- 单杯成交价快照
);
GO

/* ###########################################################################
   批次 5：补货单头（引用批次 1 的员工）
   ########################################################################### */

/* ===========================================================================
   9. inv_restock 补货单（一行 = 一次补货采购·单头）      docs/02 §2.2-10
   =========================================================================== */
CREATE TABLE dbo.inv_restock
(
    restock_id     CHAR(8)       NOT NULL,   -- 主码，R + 7 位序号
    supplier_name  NVARCHAR(100) NOT NULL,   -- 供应商仅记名称，不建表
    restock_status VARCHAR(20)   NOT NULL
        CONSTRAINT DF_inv_restock_restock_status DEFAULT ('PENDING'),
    employee_id    CHAR(8)       NOT NULL,   -- 经办
    created_at     DATETIME2(0)  NOT NULL,   -- 下单时间
    received_at    DATETIME2(0)  NULL        -- 验收入库时间
);
GO

/* ###########################################################################
   批次 6：引用批次 3—5 的关系表（可任意顺序）
   ########################################################################### */

/* ===========================================================================
   10. order_item_addon 加料明细（一行 = 某杯的一种加料及数量）
                                                          docs/02 §2.2-7
   =========================================================================== */
CREATE TABLE dbo.order_item_addon
(
    order_id    CHAR(12) NOT NULL,
    item_line   SMALLINT NOT NULL,
    material_id CHAR(8)  NOT NULL,
    qty         INT      NOT NULL    -- 该行该加料总份数
);
GO

/* ===========================================================================
   11. order_platform 平台订单信息（弱实体，一行 = 一单的平台侧信息）
                                                          docs/02 §2.2-8
   =========================================================================== */
CREATE TABLE dbo.order_platform
(
    order_id          CHAR(12)      NOT NULL,  -- 主码兼外码
    platform_order_no VARCHAR(50)   NOT NULL,  -- 平台外部单号（幂等键）
    platform_name     VARCHAR(20)   NOT NULL,
    delivery_address  NVARCHAR(200) NOT NULL,
    delivery_fee      DECIMAL(10,2) NOT NULL CONSTRAINT DF_order_platform_delivery_fee    DEFAULT (0),
    commission_rate   DECIMAL(5,4)  NOT NULL CONSTRAINT DF_order_platform_commission_rate DEFAULT (0.2000),
    commission_amount DECIMAL(10,2) NOT NULL   -- = 商品金额 × commission_rate
);
GO

/* ===========================================================================
   12. inv_restock_item 补货明细（一行 = 该次补货的一种原料及数量）
                                                          docs/02 §2.2-11
   =========================================================================== */
CREATE TABLE dbo.inv_restock_item
(
    restock_id  CHAR(8)       NOT NULL,
    material_id CHAR(8)       NOT NULL,
    qty         DECIMAL(10,2) NOT NULL    -- 补货数量
);
GO

/* ###########################################################################
   批次 7：库存流水（跨批次引用最多：原料 + 订单 + 补货单 + 员工）
   ########################################################################### */

/* ===========================================================================
   13. inv_stock_log 库存流水（一行 = 一次库存变动）      docs/02 §2.2-9
   =========================================================================== */
CREATE TABLE dbo.inv_stock_log
(
    stock_log_id BIGINT IDENTITY(1,1) NOT NULL,  -- 代理键（事件日志，无人读主键）
    material_id  CHAR(8)       NOT NULL,
    log_type     VARCHAR(10)   NOT NULL,   -- IN/SALE/LOSS/GAIN/SHORT
    qty          DECIMAL(10,2) NOT NULL,   -- 数量绝对值，方向由 log_type 决定
    order_id     CHAR(12)      NULL,       -- SALE 关联订单；入库/损耗为 NULL
    restock_id   CHAR(8)       NULL,       -- IN 关联补货单；销售流水为 NULL
    employee_id  CHAR(8)       NOT NULL,   -- 经办
    created_at   DATETIME2(0)  NOT NULL    -- 变动时间
);
GO

/* ###########################################################################
   批次 8：会员两类流水（引用批次 1 会员、批次 3 订单）
   ########################################################################### */

/* ===========================================================================
   14. member_point_log 积分流水（一行 = 一次积分变动）   docs/02 §2.2-13
   =========================================================================== */
CREATE TABLE dbo.member_point_log
(
    point_log_id BIGINT IDENTITY(1,1) NOT NULL,  -- 代理键
    member_id    CHAR(8)      NOT NULL,
    point_type   VARCHAR(20)  NOT NULL,   -- EARN_SALE/EARN_RECHARGE/REVERSAL
    points       INT          NOT NULL,   -- 绝对值，方向由 point_type 决定
    order_id     CHAR(12)     NULL,       -- 充值计分为 NULL
    created_at   DATETIME2(0) NOT NULL
);
GO

/* ===========================================================================
   15. member_balance_log 储值流水（一行 = 一次余额变动） docs/02 §2.2-14
   =========================================================================== */
CREATE TABLE dbo.member_balance_log
(
    balance_log_id BIGINT IDENTITY(1,1) NOT NULL,  -- 代理键
    member_id      CHAR(8)       NOT NULL,
    balance_type   VARCHAR(20)   NOT NULL,  -- RECHARGE/SPEND/REFUND
    amount         DECIMAL(10,2) NOT NULL,  -- 绝对值，方向由 balance_type 决定
    order_id       CHAR(12)      NULL,      -- 充值为 NULL
    external_ref   VARCHAR(64)   NULL,      -- 充值外部参考号（幂等键）
    created_at     DATETIME2(0)  NOT NULL
);
GO

/* ###########################################################################
   批次 9：券规则（无外码）
   ########################################################################### */

/* ===========================================================================
   16. mkt_coupon_rule 券规则（一行 = 一种券的定义）      docs/02 §2.2-15
   =========================================================================== */
CREATE TABLE dbo.mkt_coupon_rule
(
    coupon_rule_id   CHAR(8)       NOT NULL,  -- 主码，CP + 6 位序号
    coupon_name      NVARCHAR(100) NOT NULL,
    coupon_type      VARCHAR(20)   NOT NULL,  -- 本版仅满减一种；10→20 见 docs/02 §5.7 #3
    threshold_amount DECIMAL(10,2) NOT NULL,  -- 满 X
    discount_amount  DECIMAL(10,2) NOT NULL,  -- 减 Y
    valid_from       DATE          NOT NULL,  -- 纯日期用 DATE，不存伪时间
    valid_to         DATE          NOT NULL
);
GO

/* ###########################################################################
   批次 10：会员持券（引用批次 9 券规则、批次 1 会员、批次 3 订单）
   ########################################################################### */

/* ===========================================================================
   17. mkt_member_coupon 会员持券（一行 = 一会员持有的一张券）
                                                          docs/02 §2.2-16
   =========================================================================== */
CREATE TABLE dbo.mkt_member_coupon
(
    member_coupon_id BIGINT IDENTITY(1,1) NOT NULL,  -- 代理键
    coupon_rule_id   CHAR(8)      NOT NULL,
    member_id        CHAR(8)      NOT NULL,
    coupon_status    VARCHAR(10)  NOT NULL
        CONSTRAINT DF_mkt_member_coupon_coupon_status DEFAULT ('UNUSED'),
    used_order_id    CHAR(12)     NULL,   -- 核销在哪单；未使用为 NULL
    issued_at        DATETIME2(0) NOT NULL
);
GO

/* ===========================================================================
   建表结果自检：输出可直接贴进 evidence/ 作为运行证据
   ---------------------------------------------------------------------------
   本脚本只建「列 + 默认值」，故只统计列数与默认值数。主码/候选码/外码/检查约束
   的统计在 sql/constraint.sql 的尾部汇总里（那里才看得到完整 219 项指纹）。
   =========================================================================== */
SELECT
      t.name AS table_name
    , (SELECT COUNT(*) FROM sys.columns             c  WHERE c.object_id        = t.object_id) AS column_count
    , (SELECT COUNT(*) FROM sys.default_constraints d  WHERE d.parent_object_id = t.object_id) AS default_count
FROM sys.tables t
ORDER BY t.name;

-- 汇总：17 张表、110 列、16 条 DEFAULT（主码/候选码/外码/检查约束见 constraint.sql）
SELECT
      (SELECT COUNT(*) FROM sys.tables)             AS total_tables
    , (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) AS total_columns
    , (SELECT COUNT(*) FROM sys.default_constraints) AS total_default;

PRINT N'01-schema: 17 张表已按外码拓扑序建成（列 + 默认值，批次 1—10）。';
PRINT N'01-schema: 下一步执行 sql/constraint.sql（主码/候选码/外码/检查约束）。';
GO

/* ===========================================================================
   键与约束（主码 / 候选码 / 外码 / 检查约束）已全部移至 sql/constraint.sql（第 4 周
   重构）。原"故意没写的 2 项""另记 1 处文档待修"等注记一并随约束移至
   constraint.sql 尾部，本文本不再重复。
   =========================================================================== */
