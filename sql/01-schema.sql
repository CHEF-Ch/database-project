/* ============================================================================
   01-schema.sql —— 建表（17 张，按外码拓扑序）
   ----------------------------------------------------------------------------
   前置：必须先跑 sql/00-create.sql（本脚本【要求空库】）。
   依据：docs/02-数据字典.md §2.2 的 17 张表字段定义，逐字段对齐；
         外码依据 §3.1 关联总表；删除规则依据 §3.3。

   ★ 建表顺序 = docs/06-进度/第三周任务流程与状态.md §二 的外码拓扑序：
     批次 1  item_product, item_material, member_member, staff_employee（无外码）
     批次 2  item_recipe, item_addon_option
     批次 3  shop_order
     批次 4  order_item
     批次 5  inv_restock
     批次 6  order_item_addon, order_platform, inv_restock_item
     批次 7  inv_stock_log
     批次 8  member_point_log, member_balance_log
     批次 9  mkt_coupon_rule
     批次 10 mkt_member_coupon
     ——没有任何一张表被它后面才出现的表引用，故从头顺序执行即可跑通。
     删除时按相反顺序（10 → 1），即课程提示第 1 条。

   ★ 本脚本【不可重复执行】：表已存在会报"对象已存在"。要重跑请先重跑
     00-create.sql 把库清空——这正是本目录的复现口径（见 sql/README.md）。

   ★ 键与约束的命名（**延伸了 docs/06/第二周 §五 的规则，需回填文档**）：
     主码 PK_表名        外码 FK_子表_父表        候选码 UQ_表名_字段名
     检查 CK_表名_字段名  默认 DF_表名_字段名
     后两类是本次新增的，理由不是"好看"而是**复现必需**：不显式命名的 DEFAULT /
     CHECK 会由 SQL Server 自动起名（如 DF__item_pro__sale___1A2B3C4D），后缀是
     随机的十六进制，两次复现建出的约束名就不一样，"得到相同的表结构"这条要求
     当场不成立。

   ★ CHECK 的取舍口径（本脚本统一按此执行，便于审计）：
     · 列级 CHECK **本周写齐**——枚举取值（docs/02 §1.1 共 18 项）与
       "> 0 / >= 0" 这类单列取值范围（docs/06/第二周 §六.8）。
     · 行级/跨列 CHECK **留给第 4 周**——如 CHECK (reserved_qty <= stock_qty)，
       依据是 docs/06/第二周 §六.8 末段"属第 4 周""此处仅记录，不写 SQL"。
   ============================================================================ */

SET NOCOUNT ON;

/* ---------------------------------------------------------------------------
   下面两条 SET 必须显式写死，不能依赖客户端默认值。
   原因：本脚本用了**带筛选条件的唯一索引**（见 UQ_member_member_phone），
   CREATE INDEX 要求创建时 QUOTED_IDENTIFIER 与 ANSI_NULLS 均为 ON。
   而 sqlcmd 默认 QUOTED_IDENTIFIER = **OFF**，SSMS 默认 = ON。不写死的话，
   同一个脚本会出现"在 SSMS 里能跑、按 sql/README.md 的 sqlcmd 命令却报
   Msg 1934: CREATE INDEX failed because the following SET options have
   incorrect settings: 'QUOTED_IDENTIFIER'"——已验证。复现要求换工具也得到
   同样结果，故在脚本内固定。
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
        CONSTRAINT DF_item_product_sale_status DEFAULT ('ON_SALE'),

    CONSTRAINT PK_item_product             PRIMARY KEY (product_id),
    -- 候选码：同款同杯型唯一（docs/02 §5.2）
    CONSTRAINT UQ_item_product_name_size   UNIQUE (name, size),
    CONSTRAINT CK_item_product_size        CHECK (size IN ('SMALL', 'MEDIUM', 'LARGE')),
    CONSTRAINT CK_item_product_sale_status CHECK (sale_status IN ('ON_SALE', 'OFF_SHELF'))
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
    unit            VARCHAR(10)   NOT NULL,  -- 单位（克/毫升/个），只在原料表存
    material_kind   VARCHAR(10)   NOT NULL,  -- INGREDIENT 原料 / ADDON 加料
    stock_qty       DECIMAL(10,2) NOT NULL CONSTRAINT DF_item_material_stock_qty    DEFAULT (0),
    reserved_qty    DECIMAL(10,2) NOT NULL CONSTRAINT DF_item_material_reserved_qty DEFAULT (0),
    safety_stock    DECIMAL(10,2) NOT NULL CONSTRAINT DF_item_material_safety_stock DEFAULT (0),
    shelf_life_days INT           NULL,      -- 保质期天数；NULL = 不管控效期

    CONSTRAINT PK_item_material PRIMARY KEY (material_id),
    -- 候选码：单店原料名唯一（docs/02 §5.2）
    CONSTRAINT UQ_item_material_name            UNIQUE (name),
    CONSTRAINT CK_item_material_material_kind   CHECK (material_kind IN ('INGREDIENT', 'ADDON')),
    -- 状态型快照，可以为 0（0 = 卖完了）           docs/06/第二周 §六.8
    CONSTRAINT CK_item_material_stock_qty       CHECK (stock_qty    >= 0),
    CONSTRAINT CK_item_material_reserved_qty    CHECK (reserved_qty >= 0),
    CONSTRAINT CK_item_material_safety_stock    CHECK (safety_stock >= 0),
    -- "能放 0 天"无意义，故 > 0。列为 NULL 时 CHECK 求值为 UNKNOWN，不拦——
    -- 这正是"NULL = 不管控效期"想要的行为，无需额外写 IS NULL OR ...。
    CONSTRAINT CK_item_material_shelf_life_days CHECK (shelf_life_days > 0)
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
    created_at DATETIME2(0)  NOT NULL,   -- 入会时间

    CONSTRAINT PK_member_member PRIMARY KEY (member_id),
    -- 余额不能透支必须拦得住（docs/02 §2.1 字段级决定 4）
    CONSTRAINT CK_member_member_points  CHECK (points  >= 0),
    CONSTRAINT CK_member_member_balance CHECK (balance >= 0)
);
GO

/* 候选码 phone：**可空唯一**。
   SQL Server 的 UNIQUE 约束只允许存在一个 NULL，这与 docs/02 §5.2"手机号唯一；
   微信未绑手机为空（可空，多 NULL 不冲突）"直接矛盾——实测第二个未绑手机的会员
   就会报 "Violation of UNIQUE KEY constraint"。
   故改用**带筛选条件的唯一索引**（WHERE ... IS NOT NULL）：这才是"唯一、但允许
   多个 NULL"在 SQL Server 里的正确实现。详见 docs/02 §5.7 #4。 */
CREATE UNIQUE INDEX UQ_member_member_phone
    ON dbo.member_member (phone) WHERE phone IS NOT NULL;
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
        CONSTRAINT DF_staff_employee_employ_status DEFAULT ('ACTIVE'),

    -- 候选码：无（同名员工存在，靠代理键区分）      docs/02 §5.2
    CONSTRAINT PK_staff_employee PRIMARY KEY (employee_id),
    CONSTRAINT CK_staff_employee_job_title     CHECK (job_title     IN ('CASHIER', 'MAKER', 'STOCK_KEEPER', 'MANAGER')),
    CONSTRAINT CK_staff_employee_employ_status CHECK (employ_status IN ('ACTIVE', 'RESIGNED'))
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
    qty         DECIMAL(10,2) NOT NULL,   -- 每杯标准用量（单位继承原料）

    -- 复合主码即自然码，故无独立候选码（docs/02 §5.2）
    CONSTRAINT PK_item_recipe PRIMARY KEY (product_id, material_id),
    CONSTRAINT FK_item_recipe_item_product  FOREIGN KEY (product_id)  REFERENCES dbo.item_product  (product_id),
    CONSTRAINT FK_item_recipe_item_material FOREIGN KEY (material_id) REFERENCES dbo.item_material (material_id),
    -- 事件型：用量 0 表示"发生了 0 次"，语义自相矛盾，那一行本就不该存在
    --                                                docs/06/第二周 §六.8
    CONSTRAINT CK_item_recipe_qty CHECK (qty > 0)
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
    addon_status VARCHAR(20)   NOT NULL  -- UNAVAILABLE 长 11，10→20 见 docs/02 §5.7 #3
        CONSTRAINT DF_item_addon_option_addon_status DEFAULT ('AVAILABLE'),

    CONSTRAINT PK_item_addon_option PRIMARY KEY (product_id, material_id),
    CONSTRAINT FK_item_addon_option_item_product  FOREIGN KEY (product_id)  REFERENCES dbo.item_product  (product_id),
    CONSTRAINT FK_item_addon_option_item_material FOREIGN KEY (material_id) REFERENCES dbo.item_material (material_id),
    CONSTRAINT CK_item_addon_option_addon_price  CHECK (addon_price > 0),
    CONSTRAINT CK_item_addon_option_addon_qty    CHECK (addon_qty   > 0),
    CONSTRAINT CK_item_addon_option_addon_status CHECK (addon_status IN ('AVAILABLE', 'UNAVAILABLE'))
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
    refund_at       DATETIME2(0)  NULL,

    CONSTRAINT PK_shop_order PRIMARY KEY (order_id),
    CONSTRAINT FK_shop_order_member_member  FOREIGN KEY (member_id)   REFERENCES dbo.member_member  (member_id),
    CONSTRAINT FK_shop_order_staff_employee FOREIGN KEY (employee_id) REFERENCES dbo.staff_employee (employee_id),
    CONSTRAINT CK_shop_order_channel      CHECK (channel      IN ('COUNTER', 'PLATFORM')),
    -- order_mode / pay_method 可空：NULL 求值为 UNKNOWN，CHECK 不拦，
    -- 正好表达"平台单无 order_mode""未付款无 pay_method"。
    CONSTRAINT CK_shop_order_order_mode   CHECK (order_mode   IN ('COUNTER_CASHIER', 'COUNTER_SELF')),
    CONSTRAINT CK_shop_order_order_status CHECK (order_status IN ('PENDING', 'PAID', 'MAKING', 'COMPLETED', 'CANCELLED', 'REFUNDED')),
    CONSTRAINT CK_shop_order_pay_method   CHECK (pay_method   IN ('CASH', 'ONLINE', 'BALANCE'))
);
GO

/* 候选码 pay_txn_no：**可空唯一**（仅线上单有流水号，现金/余额/平台单为 NULL）。
   理由同 UQ_member_member_phone——UNIQUE 约束只容一个 NULL，而本项目里同时存在
   多张非线上单。改用筛选唯一索引。详见 docs/02 §5.7 #4。 */
CREATE UNIQUE INDEX UQ_shop_order_pay_txn_no
    ON dbo.shop_order (pay_txn_no) WHERE pay_txn_no IS NOT NULL;
GO

/* ###########################################################################
   批次 4：订单明细（引用批次 3 的订单、批次 1 的商品）
   ########################################################################### */

/* ===========================================================================
   8. order_item 订单明细（一行 = 一款饮品及其杯数）      docs/02 §2.2-6
      弱实体：订单删除时随之消失，故外码带 ON DELETE CASCADE（docs/02 §3.3）。
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
    unit_price  DECIMAL(10,2) NOT NULL,   -- 单杯成交价快照

    CONSTRAINT PK_order_item PRIMARY KEY (order_id, item_line),
    CONSTRAINT FK_order_item_shop_order   FOREIGN KEY (order_id)   REFERENCES dbo.shop_order   (order_id)   ON DELETE CASCADE,
    CONSTRAINT FK_order_item_item_product FOREIGN KEY (product_id) REFERENCES dbo.item_product (product_id),
    CONSTRAINT CK_order_item_size        CHECK (size        IN ('SMALL', 'MEDIUM', 'LARGE')),
    CONSTRAINT CK_order_item_sugar_level CHECK (sugar_level IN ('FULL', 'SEVENTY', 'HALF', 'THIRTY', 'NONE')),
    CONSTRAINT CK_order_item_ice_level   CHECK (ice_level   IN ('REGULAR', 'LESS', 'NONE')),
    CONSTRAINT CK_order_item_temp_level  CHECK (temp_level  IN ('HOT', 'COLD')),
    -- 事件型：杯数必须 > 0（docs/06/第二周 §六.8）
    CONSTRAINT CK_order_item_qty         CHECK (qty > 0)
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
    received_at    DATETIME2(0)  NULL,       -- 验收入库时间

    -- 候选码：无                                          docs/02 §5.2
    CONSTRAINT PK_inv_restock PRIMARY KEY (restock_id),
    CONSTRAINT FK_inv_restock_staff_employee FOREIGN KEY (employee_id) REFERENCES dbo.staff_employee (employee_id),
    CONSTRAINT CK_inv_restock_restock_status CHECK (restock_status IN ('PENDING', 'RECEIVED', 'CANCELLED'))
);
GO

/* ###########################################################################
   批次 6：引用批次 3—5 的关系表（可任意顺序）
   ########################################################################### */

/* ===========================================================================
   10. order_item_addon 加料明细（一行 = 某杯的一种加料及数量）
                                                          docs/02 §2.2-7
      弱实体：依附订单明细行存在，故外码带 ON DELETE CASCADE（docs/02 §3.3）。
   =========================================================================== */
CREATE TABLE dbo.order_item_addon
(
    order_id    CHAR(12) NOT NULL,
    item_line   SMALLINT NOT NULL,
    material_id CHAR(8)  NOT NULL,
    qty         INT      NOT NULL,   -- 该行该加料总份数

    CONSTRAINT PK_order_item_addon PRIMARY KEY (order_id, item_line, material_id),
    -- 复合外码指向 order_item 的复合主码
    CONSTRAINT FK_order_item_addon_order_item    FOREIGN KEY (order_id, item_line) REFERENCES dbo.order_item    (order_id, item_line) ON DELETE CASCADE,
    CONSTRAINT FK_order_item_addon_item_material FOREIGN KEY (material_id)         REFERENCES dbo.item_material (material_id),
    -- 加料数量必须 > 0：0 会在统计中虚增"加料被点次数"（COUNT(*) 会计入它），
    -- 且该行在金额与库存上完全隐形（docs/06/第二周 §六.8）
    CONSTRAINT CK_order_item_addon_qty CHECK (qty > 0)
);
GO

/* ===========================================================================
   11. order_platform 平台订单信息（弱实体，一行 = 一单的平台侧信息）
                                                          docs/02 §2.2-8
      主码即外码（order_id）——平台信息不可能脱离订单存在，1:0..1 天然成立，
      无需额外 UQ（docs/02 §2.1 字段级决定 3）。
   =========================================================================== */
CREATE TABLE dbo.order_platform
(
    order_id          CHAR(12)      NOT NULL,  -- 主码兼外码
    platform_order_no VARCHAR(50)   NOT NULL,  -- 平台外部单号（幂等键）
    platform_name     VARCHAR(20)   NOT NULL,
    delivery_address  NVARCHAR(200) NOT NULL,
    delivery_fee      DECIMAL(10,2) NOT NULL CONSTRAINT DF_order_platform_delivery_fee    DEFAULT (0),
    commission_rate   DECIMAL(5,4)  NOT NULL CONSTRAINT DF_order_platform_commission_rate DEFAULT (0.2000),
    commission_amount DECIMAL(10,2) NOT NULL,  -- = 商品金额 × commission_rate

    CONSTRAINT PK_order_platform PRIMARY KEY (order_id),
    -- 候选码：平台外部单号唯一，防重（docs/02 §5.2）
    CONSTRAINT UQ_order_platform_platform_order_no UNIQUE (platform_order_no),
    CONSTRAINT FK_order_platform_shop_order FOREIGN KEY (order_id) REFERENCES dbo.shop_order (order_id) ON DELETE CASCADE,
    CONSTRAINT CK_order_platform_platform_name CHECK (platform_name IN ('MEITUAN', 'ELEME'))
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
    qty         DECIMAL(10,2) NOT NULL,   -- 补货数量

    CONSTRAINT PK_inv_restock_item PRIMARY KEY (restock_id, material_id),
    CONSTRAINT FK_inv_restock_item_inv_restock   FOREIGN KEY (restock_id)  REFERENCES dbo.inv_restock  (restock_id),
    CONSTRAINT FK_inv_restock_item_item_material FOREIGN KEY (material_id) REFERENCES dbo.item_material (material_id),
    CONSTRAINT CK_inv_restock_item_qty CHECK (qty > 0)
);
GO

/* ###########################################################################
   批次 7：库存流水（跨批次引用最多：原料 + 订单 + 补货单 + 员工）
   ########################################################################### */

/* ===========================================================================
   13. inv_stock_log 库存流水（一行 = 一次库存变动）      docs/02 §2.2-9
      审计依据：对应原料即便停用，流水仍保留，故外码【不带】级联（docs/02 §3.3）。
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
    created_at   DATETIME2(0)  NOT NULL,   -- 变动时间

    CONSTRAINT PK_inv_stock_log PRIMARY KEY (stock_log_id),
    CONSTRAINT FK_inv_stock_log_item_material  FOREIGN KEY (material_id) REFERENCES dbo.item_material (material_id),
    CONSTRAINT FK_inv_stock_log_shop_order     FOREIGN KEY (order_id)    REFERENCES dbo.shop_order    (order_id),
    CONSTRAINT FK_inv_stock_log_inv_restock    FOREIGN KEY (restock_id)  REFERENCES dbo.inv_restock   (restock_id),
    CONSTRAINT FK_inv_stock_log_staff_employee FOREIGN KEY (employee_id) REFERENCES dbo.staff_employee (employee_id),
    CONSTRAINT CK_inv_stock_log_log_type CHECK (log_type IN ('IN', 'SALE', 'LOSS', 'GAIN', 'SHORT')),
    -- 流水数值存正数绝对值，方向由 type 枚举决定（docs/02 §5.4）
    CONSTRAINT CK_inv_stock_log_qty      CHECK (qty > 0)
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
    created_at   DATETIME2(0) NOT NULL,

    CONSTRAINT PK_member_point_log PRIMARY KEY (point_log_id),
    CONSTRAINT FK_member_point_log_member_member FOREIGN KEY (member_id) REFERENCES dbo.member_member (member_id),
    CONSTRAINT FK_member_point_log_shop_order    FOREIGN KEY (order_id)  REFERENCES dbo.shop_order    (order_id),
    CONSTRAINT CK_member_point_log_point_type CHECK (point_type IN ('EARN_SALE', 'EARN_RECHARGE', 'REVERSAL')),
    CONSTRAINT CK_member_point_log_points     CHECK (points > 0)
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
    created_at     DATETIME2(0)  NOT NULL,

    CONSTRAINT PK_member_balance_log PRIMARY KEY (balance_log_id),
    CONSTRAINT FK_member_balance_log_member_member  FOREIGN KEY (member_id) REFERENCES dbo.member_member (member_id),
    CONSTRAINT FK_member_balance_log_shop_order     FOREIGN KEY (order_id)  REFERENCES dbo.shop_order    (order_id),
    CONSTRAINT CK_member_balance_log_balance_type CHECK (balance_type IN ('RECHARGE', 'SPEND', 'REFUND')),
    CONSTRAINT CK_member_balance_log_amount       CHECK (amount > 0)
);
GO

/* 候选码 external_ref：**可空唯一**（仅充值有外部参考号，消费/退款为 NULL）。
   理由同 UQ_member_member_phone。详见 docs/02 §5.7 #4。 */
CREATE UNIQUE INDEX UQ_member_balance_log_external_ref
    ON dbo.member_balance_log (external_ref) WHERE external_ref IS NOT NULL;
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
    valid_to         DATE          NOT NULL,

    -- 候选码：无（同规则可建多张）                        docs/02 §5.2
    CONSTRAINT PK_mkt_coupon_rule PRIMARY KEY (coupon_rule_id),
    CONSTRAINT CK_mkt_coupon_rule_coupon_type CHECK (coupon_type IN ('FULL_REDUCTION'))
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
    issued_at        DATETIME2(0) NOT NULL,

    -- 候选码：无（同会员可持同种券多张）                  docs/02 §5.2
    CONSTRAINT PK_mkt_member_coupon PRIMARY KEY (member_coupon_id),
    CONSTRAINT FK_mkt_member_coupon_mkt_coupon_rule FOREIGN KEY (coupon_rule_id) REFERENCES dbo.mkt_coupon_rule (coupon_rule_id),
    CONSTRAINT FK_mkt_member_coupon_member_member   FOREIGN KEY (member_id)      REFERENCES dbo.member_member  (member_id),
    CONSTRAINT FK_mkt_member_coupon_shop_order      FOREIGN KEY (used_order_id)  REFERENCES dbo.shop_order     (order_id),
    CONSTRAINT CK_mkt_member_coupon_coupon_status CHECK (coupon_status IN ('UNUSED', 'LOCKED', 'USED', 'RETURNED'))
);
GO

/* ===========================================================================
   建表结果自检：输出可直接贴进 evidence/ 作为运行证据
   ---------------------------------------------------------------------------
   注意：这里输出的**不是表结构本身**，而是"每张表建出了多少列 / 多少个码 /
   多少个约束"的统计数，用来与 docs/02 §2.2 逐张核对。
   要看列清单请查 sys.columns；要看约束定义请查 sys.check_constraints 等。
   =========================================================================== */
SELECT
      t.name AS table_name
    , (SELECT COUNT(*) FROM sys.columns             c  WHERE c.object_id        = t.object_id) AS column_count
    , (SELECT COUNT(*) FROM sys.key_constraints     k  WHERE k.parent_object_id = t.object_id AND k.type = 'PK') AS pk_count
    , (SELECT COUNT(*) FROM sys.foreign_keys        f  WHERE f.parent_object_id = t.object_id) AS fk_count
    , (SELECT COUNT(*) FROM sys.check_constraints   cc WHERE cc.parent_object_id = t.object_id) AS check_count
    , (SELECT COUNT(*) FROM sys.default_constraints d  WHERE d.parent_object_id = t.object_id) AS default_count
    -- 候选码有两种实现：NOT NULL 列用 UNIQUE 约束；可空列用筛选唯一索引
    -- （见 UQ_member_member_phone 处说明）。此处一并计数，故 unique_count 仍为 6。
    , (  (SELECT COUNT(*) FROM sys.key_constraints u WHERE u.parent_object_id = t.object_id AND u.type = 'UQ')
       + (SELECT COUNT(*) FROM sys.indexes i WHERE i.object_id = t.object_id
              AND i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0)) AS unique_count
FROM sys.tables t
ORDER BY t.name;

-- 汇总：与 docs/02 §2.2 对表（17 张表、25 条外码、18 项枚举 CHECK）
SELECT
      (SELECT COUNT(*) FROM sys.tables)             AS total_tables
    , (SELECT COUNT(*) FROM sys.foreign_keys)       AS total_fk
    , (SELECT COUNT(*) FROM sys.check_constraints)  AS total_check
    , (SELECT COUNT(*) FROM sys.default_constraints) AS total_default
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') AS total_pk
    -- 注意必须限定到用户表：sys.indexes 在用户库里还包含 sqlagent_* / plan_persist_*
    -- / queue_* 等系统内部表的索引，不加 i.object_id IN (SELECT object_id FROM
    -- sys.tables) 会数出 170 这种荒唐值（实测踩过）。
    , (  (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ')
       + (SELECT COUNT(*) FROM sys.indexes i WHERE i.is_unique = 1 AND i.is_primary_key = 0
              AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0
              AND i.object_id IN (SELECT object_id FROM sys.tables))) AS total_uq;

PRINT N'01-schema: 17 张表已按外码拓扑序建成（批次 1—10，见 docs/06/第三周 §二）。';
PRINT N'01-schema: 下一步执行 sql/constraint.sql（CHECK 演示）或 sql/02-seed.sql（装载样例数据）。';
GO

/* ===========================================================================
   附：本脚本【故意没写】的 3 项，以及原因（避免被当成漏写）
   ---------------------------------------------------------------------------
   1. CHECK (reserved_qty <= stock_qty)
      行级跨列约束，docs/06/第二周 §六.8 末段明确"属第 4 周"、"此处仅记录这是一条
      约束，不写 SQL"。第 4 周由 constraint.sql 统一补。

   2. CHECK (unit_price > 0) 之类"文档没列、但显然该有"的约束
      docs/02 §2.2 给 unit_price 的约束只写了"非空"，§1.1 与第二周 §六.8 也未列
      售价。docs/06/第三周 §三.2 要求"脚本与文档必须一致，不能两个版本并存"，
      故不擅自加。同类还有：total_amount >= 0、delivery_fee >= 0、
      valid_to >= valid_from、threshold_amount > 0 等——都要先回填 docs/02 再改脚本。

   3. item_addon_option.material_id "须可作加料"（docs/02 §2.2-4 括注）
      跨表规则（要读 item_material.material_kind），CHECK 表达不了，需触发器或
      标量 UDF。按 docs/06/第三周 §三.3"记录下来并回填文档"处理，此处不写。
      同类还有：shop_order 上"channel=PLATFORM 时 order_mode 必为 NULL"、
      "order_status=PENDING 时 pay_method 必为 NULL"等业务规则。

   另记 2 处文档待修：
   · docs/02 §1.1 第 8 项只列了 order_item.size 的杯型取值，而 item_product.size
     用的是同一域（§2.2-1 写作"杯型 SMALL/MEDIUM/LARGE"），本脚本按同域加了
     CK_item_product_size。请把 §1.1 #8 补成两处同域（参照 #9 把三个字段并成
     一行的写法），否则文档与脚本又对不上了。
   · docs/06/第三周 §二 写"由 docs/02 §3.1 的 24 条关联推出"，但 §3.1 实际有
     25 行（#1—#25）。本脚本据此建 25 条外码，与 §3.1 一致；§二 的"24 条"需改。
   =========================================================================== */
