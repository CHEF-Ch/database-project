-- ============================================================
-- 01-schema.sql —— 建表脚本（批次 1 / 共 10 批）
-- 前提：已执行 00-drop.sql + 00-create.sql，当前库 BubbleTeaShop 为空库。
-- 顺序：按外码拓扑序——先建被引用的表，再建引用它的表（见 docs/06 第三周 §二）。
-- 本批：4 张无外码的主数据表，彼此无依赖，可任意顺序。
-- ============================================================

USE BubbleTeaShop;
GO

-- 会话选项：筛选唯一索引（见 shop_order / member_member / member_balance_log 表后）
-- 要求这两个选项为 ON，且**在建索引时就已生效**。不依赖客户端的默认值——
-- sqlcmd 不同版本/调用方式默认值不一致，不显式设置会报
-- 错误 1934「CREATE INDEX 失败，因为下列 SET 选项的设置不正确: 'QUOTED_IDENTIFIER'」。
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- ------------------------------------------------------------
-- 批次 1-1　item_product　商品（一行 = 一款可售饮品）
-- 粒度说明：杯型是商品固有属性 → 不同杯型是**不同的行**，故 (name, size) 才能唯一。
-- ------------------------------------------------------------
CREATE TABLE item_product (
    product_id   CHAR(8)        NOT NULL,                       -- 主码：P + 7 位序号
    name         NVARCHAR(100)  NOT NULL,                       -- 商品名（不含杯型）
    size         VARCHAR(10)    NOT NULL,                       -- SMALL / MEDIUM / LARGE
    unit_price   DECIMAL(10,2)  NOT NULL,                       -- 售价
    sale_status  VARCHAR(10)    NOT NULL CONSTRAINT DF_item_product_sale_status DEFAULT 'ON_SALE',

    CONSTRAINT PK_item_product        PRIMARY KEY (product_id),
    CONSTRAINT UQ_item_product_name_size UNIQUE (name, size),
    CONSTRAINT CK_item_product_size   CHECK (size IN ('SMALL','MEDIUM','LARGE')),
    CONSTRAINT CK_item_product_status CHECK (sale_status IN ('ON_SALE','OFF_SHELF')),
    CONSTRAINT CK_item_product_price  CHECK (unit_price > 0)
);
GO

-- ------------------------------------------------------------
-- 批次 1-2　item_material　原料/加料（一行 = 一种原料）
-- 粒度说明：珍珠既可作配方原料、又可作加料，是"同一实体的两种角色"，
--          故合并为一张表，用 material_kind 区分——不是两张表。
-- ------------------------------------------------------------
CREATE TABLE item_material (
    material_id      CHAR(8)        NOT NULL,                   -- 主码：M + 7 位序号
    name             NVARCHAR(100)  NOT NULL,                   -- 原料名
    unit             NVARCHAR(10)   NOT NULL,                   -- 克/毫升/个；单位只在此表存
    material_kind    VARCHAR(10)    NOT NULL,                   -- INGREDIENT / ADDON
    stock_qty        DECIMAL(10,2)  NOT NULL CONSTRAINT DF_item_material_stock    DEFAULT 0,
    reserved_qty     DECIMAL(10,2)  NOT NULL CONSTRAINT DF_item_material_reserved DEFAULT 0,
    safety_stock     DECIMAL(10,2)  NOT NULL CONSTRAINT DF_item_material_safety   DEFAULT 0,
    shelf_life_days  INT            NULL,                       -- 空 = 不管控效期

    CONSTRAINT PK_item_material           PRIMARY KEY (material_id),
    CONSTRAINT UQ_item_material_name      UNIQUE (name),
    CONSTRAINT CK_item_material_kind      CHECK (material_kind IN ('INGREDIENT','ADDON')),
    CONSTRAINT CK_item_material_unit      CHECK (unit IN (N'克', N'毫升', N'个')),
    CONSTRAINT CK_item_material_stock     CHECK (stock_qty    >= 0),
    CONSTRAINT CK_item_material_reserved  CHECK (reserved_qty >= 0),
    CONSTRAINT CK_item_material_safety    CHECK (safety_stock >= 0),
    CONSTRAINT CK_item_material_shelflife CHECK (shelf_life_days IS NULL OR shelf_life_days > 0)
);
GO

-- ------------------------------------------------------------
-- 批次 1-3　member_member　会员（一行 = 一位会员，含积分/余额快照）
-- 粒度说明：积分与余额是"当前值快照"，变动历史另存 member_point_log /
--          member_balance_log。快照供快速读取与第 4 周 CHECK 落点。
-- ------------------------------------------------------------
CREATE TABLE member_member (
    member_id   CHAR(8)        NOT NULL,                        -- 主码：C + 7 位序号
    phone       VARCHAR(20)    NULL,                            -- 手机号（微信未绑手机可空）
    name        NVARCHAR(100)  NULL,                            -- 姓名/昵称
    points      INT            NOT NULL CONSTRAINT DF_member_member_points  DEFAULT 0,
    balance     DECIMAL(10,2)  NOT NULL CONSTRAINT DF_member_member_balance DEFAULT 0,
    created_at  DATETIME2(0)   NOT NULL,                        -- 入会时间

    CONSTRAINT PK_member_member         PRIMARY KEY (member_id),
    CONSTRAINT CK_member_member_points  CHECK (points  >= 0),
    CONSTRAINT CK_member_member_balance CHECK (balance >= 0)
);
GO

-- 候选码 phone：非空时唯一（微信未绑手机的会员 phone 为 NULL，允许多个）。
-- 用**筛选唯一索引**而非 `CONSTRAINT ... UNIQUE`——后者把多个 NULL 视为相等，
-- 全表最多允许一行 NULL，挡不住"多个会员都未绑手机"这一合法状态。
CREATE UNIQUE INDEX UX_member_member_phone
    ON member_member (phone)
    WHERE phone IS NOT NULL;
GO

-- ------------------------------------------------------------
-- 批次 1-4　staff_employee　员工（一行 = 一名员工）
-- 粒度说明：无候选码——同名员工真实存在，只能靠代理键区分。
-- SYSTEM 代理：'SYSTEM' 不是真人，是代表"系统/客户自助"的代理记录。
--   用途：自助单（COUNTER_SELF）无店员经手，但 shop_order.employee_id 为非空
--   （docs/02 §5.7 第 1 条：自助单扣料必产生库存流水，经办人无处可填），
--   故用本代理填该字段，避免伪造某个真人经手了客户自己下的单。
-- ------------------------------------------------------------
CREATE TABLE staff_employee (
    employee_id    CHAR(8)        NOT NULL,                     -- 主码：E + 7 位序号
    name           NVARCHAR(100)  NOT NULL,                     -- 姓名（SYSTEM 代理填 N'系统'）
    job_title      VARCHAR(20)    NOT NULL,                     -- 职务
    employ_status  VARCHAR(10)    NOT NULL CONSTRAINT DF_staff_employee_status DEFAULT 'ACTIVE',

    CONSTRAINT PK_staff_employee         PRIMARY KEY (employee_id),
    CONSTRAINT CK_staff_employee_title   CHECK (job_title IN
        ('CASHIER','MAKER','STOCK_KEEPER','MANAGER','SYSTEM')),
    CONSTRAINT CK_staff_employee_status  CHECK (employ_status IN ('ACTIVE','RESIGNED'))
);
GO

-- ------------------------------------------------------------
-- 批次 1 自检：确认 4 张表已建，且各类约束数量符合预期
-- 预期（键 = 主码 + 唯一；默认 = DEFAULT；检查 = CHECK）：
--   item_material   键 2（主码+唯一）  默认 3  检查 6
--   item_product    键 2（主码+唯一）  默认 1  检查 3
--   member_member   键 2（主码+唯一）  默认 2  检查 2
--   staff_employee  键 1（仅主码）     默认 1  检查 2
-- 合计：键 7、默认 7、检查 13
-- ------------------------------------------------------------
SELECT
    t.name        AS 表名,
    (SELECT COUNT(*) FROM sys.key_constraints     k WHERE k.parent_object_id = t.object_id) AS 键约束数,
    (SELECT COUNT(*) FROM sys.default_constraints d WHERE d.parent_object_id = t.object_id) AS 默认约束数,
    (SELECT COUNT(*) FROM sys.check_constraints   c WHERE c.parent_object_id = t.object_id) AS 检查约束数
FROM sys.tables t
WHERE t.name IN ('item_product','item_material','member_member','staff_employee')
ORDER BY t.name;
GO

-- ------------------------------------------------------------
-- 批次 2　item_recipe、item_addon_option
-- 只引用批次 1 的两张主数据表（商品 × 原料），故可先建。
-- ------------------------------------------------------------

-- 批次 2-1　item_recipe　配方（一行 = 某商品对某原料的标准用量）
-- 复合主码 (product_id, material_id) 本身就是自然码：同一商品对同一原料只有一条配方。
-- 关系属性 qty 既不属于商品也不属于原料，只属于"这一对"——故住在此表。
CREATE TABLE item_recipe (
    product_id   CHAR(8)        NOT NULL,
    material_id  CHAR(8)        NOT NULL,
    qty          DECIMAL(10,2)  NOT NULL,                       -- 每杯标准用量（单位继承原料）

    CONSTRAINT PK_item_recipe             PRIMARY KEY (product_id, material_id),
    CONSTRAINT FK_item_recipe_item_product  FOREIGN KEY (product_id)  REFERENCES item_product (product_id),
    CONSTRAINT FK_item_recipe_item_material FOREIGN KEY (material_id) REFERENCES item_material (material_id),
    CONSTRAINT CK_item_recipe_qty         CHECK (qty > 0)       -- 事件型：用量 0 = "发生了 0 次"，矛盾
);
GO

-- 批次 2-2　item_addon_option　商品-加料选项（一行 = 某商品的一种可选加料）
-- addon_price 函数依赖于 (product_id, material_id) 组合，不依赖于任一子集——
-- 同一种加料在不同商品里可以卖不同价，故单价放这里，不放 item_material。
CREATE TABLE item_addon_option (
    product_id    CHAR(8)        NOT NULL,
    material_id   CHAR(8)        NOT NULL,
    addon_price   DECIMAL(10,2)  NOT NULL,                      -- 每份加料单价
    addon_qty     DECIMAL(10,2)  NOT NULL,                      -- 每份加料标准用量（与 recipe.qty 对称）
    addon_status  VARCHAR(20)    NOT NULL CONSTRAINT DF_item_addon_option_status DEFAULT 'AVAILABLE',

    CONSTRAINT PK_item_addon_option           PRIMARY KEY (product_id, material_id),
    CONSTRAINT FK_item_addon_option_product   FOREIGN KEY (product_id)  REFERENCES item_product (product_id),
    CONSTRAINT FK_item_addon_option_material  FOREIGN KEY (material_id) REFERENCES item_material (material_id),
    CONSTRAINT CK_item_addon_option_price     CHECK (addon_price > 0),
    CONSTRAINT CK_item_addon_option_qty       CHECK (addon_qty   > 0),
    CONSTRAINT CK_item_addon_option_status    CHECK (addon_status IN ('AVAILABLE','UNAVAILABLE'))
);
GO

-- ------------------------------------------------------------
-- 批次 3　shop_order　订单（一行 = 一次交易）
-- 引用批次 1 的 member_member、staff_employee。
-- ------------------------------------------------------------
CREATE TABLE shop_order (
    order_id         CHAR(12)        NOT NULL,                  -- 主码：YYYYMMDD + 4 位当日序号
    channel          VARCHAR(10)     NOT NULL,                  -- COUNTER / PLATFORM
    order_mode       VARCHAR(20)     NULL,                      -- 仅到店单；平台单为 NULL
    order_status     VARCHAR(20)     NOT NULL,                  -- 6 状态
    member_id        CHAR(8)         NULL,                      -- 非会员/平台单为 NULL
    employee_id      CHAR(8)         NOT NULL,                  -- 经办：自助单记收银/制作员工
    item_subtotal    DECIMAL(10,2)   NOT NULL,                  -- 券前金额（商品 + 加料）
    coupon_discount  DECIMAL(10,2)   NOT NULL CONSTRAINT DF_shop_order_coupon_discount DEFAULT 0,
    total_amount     DECIMAL(10,2)   NOT NULL,                  -- 实付 = item_subtotal - coupon_discount
    pay_method       VARCHAR(20)     NULL,                      -- 未付款为 NULL
    pay_txn_no       VARCHAR(64)     NULL,                      -- 线上支付流水号（幂等键）
    created_at       DATETIME2(0)    NOT NULL,                  -- 下单时间
    paid_at          DATETIME2(0)    NULL,                      -- 支付时间
    completed_at     DATETIME2(0)    NULL,                      -- 完成时间
    refund_amount    DECIMAL(10,2)   NULL,                      -- 仅整单退款单非空
    refund_at        DATETIME2(0)    NULL,

    CONSTRAINT PK_shop_order             PRIMARY KEY (order_id),
    CONSTRAINT FK_shop_order_member      FOREIGN KEY (member_id)   REFERENCES member_member (member_id),
    CONSTRAINT FK_shop_order_employee    FOREIGN KEY (employee_id) REFERENCES staff_employee (employee_id),
    CONSTRAINT CK_shop_order_channel     CHECK (channel IN ('COUNTER','PLATFORM')),
    CONSTRAINT CK_shop_order_mode        CHECK (order_mode IS NULL
                                            OR order_mode IN ('COUNTER_CASHIER','COUNTER_SELF')),
    CONSTRAINT CK_shop_order_status      CHECK (order_status IN
        ('PENDING','PAID','MAKING','COMPLETED','CANCELLED','REFUNDED')),
    CONSTRAINT CK_shop_order_pay_method  CHECK (pay_method IS NULL
                                            OR pay_method IN ('CASH','ONLINE','BALANCE')),
    CONSTRAINT CK_shop_order_subtotal    CHECK (item_subtotal   >= 0),
    CONSTRAINT CK_shop_order_discount    CHECK (coupon_discount >= 0),
    CONSTRAINT CK_shop_order_discount_le CHECK (coupon_discount <= item_subtotal),
    CONSTRAINT CK_shop_order_total       CHECK (total_amount    >= 0),
    CONSTRAINT CK_shop_order_refund      CHECK (refund_amount IS NULL OR refund_amount > 0)
);
GO

-- 幂等键：pay_txn_no 非空时唯一（现金/余额单无线上流水号，为 NULL）。
-- **为什么不用 `CONSTRAINT ... UNIQUE (pay_txn_no)`**：SQL Server 的 UNIQUE 约束
--   把多个 NULL 视为**相等**——全表**最多只允许一行 NULL**（与 SQL 标准相反！）。
--   本表 20 行里有 14 行 pay_txn_no 为 NULL，用 UNIQUE 会在第 2 行起报
--   错误 2627「重复键值为 (<NULL>)」。实测见 docs/06 §六。
--   改用**筛选唯一索引**：只对 `pay_txn_no IS NOT NULL` 的行施加唯一，
--   既保住幂等（重复的流水号被拒），又放行任意多个 NULL。
-- 注意：筛选索引要求会话 SET QUOTED_IDENTIFIER ON（sqlcmd 默认即 ON）。
CREATE UNIQUE INDEX UX_shop_order_pay_txn_no
    ON shop_order (pay_txn_no)
    WHERE pay_txn_no IS NOT NULL;
GO

-- ------------------------------------------------------------
-- 批次 4　order_item　订单明细（一行 = 一款饮品及其杯数）
-- 引用批次 3 的 shop_order、批次 1 的 item_product。
-- 复合主码 (order_id, item_line)：item_line 是单内行号，只在单内唯一。
-- ------------------------------------------------------------
CREATE TABLE order_item (
    order_id     CHAR(12)        NOT NULL,
    item_line    SMALLINT        NOT NULL,                      -- 行号，单内自增
    product_id   CHAR(8)         NOT NULL,
    size         VARCHAR(10)     NOT NULL,                      -- 杯型快照（商品可改杯型，历史单不变）
    sugar_level  VARCHAR(10)     NOT NULL CONSTRAINT DF_order_item_sugar DEFAULT 'FULL',
    ice_level    VARCHAR(10)     NOT NULL CONSTRAINT DF_order_item_ice   DEFAULT 'REGULAR',
    temp_level   VARCHAR(10)     NOT NULL CONSTRAINT DF_order_item_temp  DEFAULT 'COLD',
    qty          INT             NOT NULL,                      -- 杯数（同款同定制合并）
    unit_price   DECIMAL(10,2)   NOT NULL,                      -- 单杯成交价快照

    CONSTRAINT PK_order_item             PRIMARY KEY (order_id, item_line),
    CONSTRAINT FK_order_item_shop_order  FOREIGN KEY (order_id)   REFERENCES shop_order (order_id) ON DELETE CASCADE,
    CONSTRAINT FK_order_item_item_product FOREIGN KEY (product_id) REFERENCES item_product (product_id),
    CONSTRAINT CK_order_item_line        CHECK (item_line > 0),
    CONSTRAINT CK_order_item_qty         CHECK (qty > 0),
    CONSTRAINT CK_order_item_size        CHECK (size IN ('SMALL','MEDIUM','LARGE')),
    CONSTRAINT CK_order_item_sugar       CHECK (sugar_level IN
        ('FULL','SEVENTY','HALF','THIRTY','NONE')),
    CONSTRAINT CK_order_item_ice         CHECK (ice_level IN ('REGULAR','LESS','NONE')),
    CONSTRAINT CK_order_item_temp        CHECK (temp_level IN ('HOT','COLD')),
    CONSTRAINT CK_order_item_price       CHECK (unit_price > 0)
);
GO

-- ------------------------------------------------------------
-- 批次 5　inv_restock　补货单（一行 = 一次补货采购·单头）
-- 引用批次 1 的 staff_employee。必须先于 inv_restock_item 与 inv_stock_log。
-- ------------------------------------------------------------
CREATE TABLE inv_restock (
    restock_id      CHAR(8)         NOT NULL,                   -- 主码：R + 7 位序号
    supplier_name   NVARCHAR(100)   NOT NULL,                   -- 供应商仅记名称，不建表
    restock_status  VARCHAR(20)     NOT NULL CONSTRAINT DF_inv_restock_status DEFAULT 'PENDING',
    employee_id     CHAR(8)         NOT NULL,                   -- 经办
    created_at      DATETIME2(0)    NOT NULL,                   -- 下单时间
    received_at     DATETIME2(0)    NULL,                       -- 验收入库时间

    CONSTRAINT PK_inv_restock            PRIMARY KEY (restock_id),
    CONSTRAINT FK_inv_restock_employee   FOREIGN KEY (employee_id) REFERENCES staff_employee (employee_id),
    CONSTRAINT CK_inv_restock_status     CHECK (restock_status IN ('PENDING','RECEIVED','CANCELLED')),
    -- 到货时间与状态互为充要：RECEIVED 必有 received_at，其余状态必无。
    -- 写法说明：用 OR 展开双向蕴含——"NULL 且非 RECEIVED" 或 "非 NULL 且 RECEIVED"，
    --   两个分支各自完整、无遗漏，等价于 (A IS NULL) ⟺ (B <> 'RECEIVED')。
    -- 为什么不用 `(A IS NULL) = (B <> 'RECEIVED')`：**T-SQL 没有布尔类型**，
    --   `a IS NULL` 是谓词（TRUE/FALSE），不是可参与 `=` 比较的表达式。
    --   实测报错 102「"="附近有语法错误」，约束建不出来（见 docs/06 §六.6）。
    --   若改用 `= NULL` 写法，结果是 UNKNOWN，而 CHECK 只拒 FALSE、放行 UNKNOWN，
    --   约束会**静默失效**——这是另一种错法，同样要避免。
    -- 文档依据：docs/02 §5.8（第 3 周回填，原文 §2.2 未写此约束）。
    CONSTRAINT CK_inv_restock_received   CHECK (
        (received_at IS NULL     AND restock_status <> 'RECEIVED')
     OR (received_at IS NOT NULL AND restock_status  = 'RECEIVED')
    )
);
GO

-- ------------------------------------------------------------
-- 批次 6　order_item_addon、order_platform、inv_restock_item
-- 分别引用批次 4、批次 3、批次 5。三张表互不依赖，可任意顺序。
-- ------------------------------------------------------------

-- 批次 6-1　order_item_addon　加料明细（一行 = 某杯的一种加料及其数量）
-- 注意外码形态：不是两个独立外码，而是一个**复合外码** (order_id, item_line)
-- 指向 order_item 的复合主码——这保证"加料必须挂在一个真实存在的订单行上"，
-- 且该行的 order_id 与本表 order_id 必然一致（不会出现"料挂在 A 单、行号却是 B 单的"）。
CREATE TABLE order_item_addon (
    order_id     CHAR(12)   NOT NULL,
    item_line    SMALLINT   NOT NULL,
    material_id  CHAR(8)    NOT NULL,
    qty          INT        NOT NULL,                           -- 该行该加料总份数

    CONSTRAINT PK_order_item_addon            PRIMARY KEY (order_id, item_line, material_id),
    CONSTRAINT FK_order_item_addon_order_item FOREIGN KEY (order_id, item_line)
        REFERENCES order_item (order_id, item_line) ON DELETE CASCADE,
    CONSTRAINT FK_order_item_addon_material   FOREIGN KEY (material_id)
        REFERENCES item_material (material_id),
    CONSTRAINT CK_order_item_addon_qty        CHECK (qty > 0)
);
GO

-- 批次 6-2　order_platform　平台订单信息（弱实体，一行 = 一单的平台侧信息）
-- 主码兼外码：order_id 既是主码又指向 shop_order。这样"一单最多一行平台信息"
-- 由主码唯一性天然保证，无需额外的 UNIQUE。
CREATE TABLE order_platform (
    order_id           CHAR(12)        NOT NULL,
    platform_order_no  VARCHAR(50)     NOT NULL,                -- 平台外部单号（幂等键）
    platform_name      VARCHAR(20)     NOT NULL,                -- MEITUAN / ELEME
    delivery_address   NVARCHAR(200)   NOT NULL,                -- 配送地址
    delivery_fee       DECIMAL(10,2)   NOT NULL CONSTRAINT DF_order_platform_fee DEFAULT 0,
    commission_rate    DECIMAL(5,4)    NOT NULL CONSTRAINT DF_order_platform_rate DEFAULT 0.2000,
    commission_amount  DECIMAL(10,2)   NOT NULL,                -- 抽成额 = total_amount × 比例

    CONSTRAINT PK_order_platform            PRIMARY KEY (order_id),
    CONSTRAINT UQ_order_platform_no         UNIQUE (platform_order_no),
    CONSTRAINT FK_order_platform_shop_order FOREIGN KEY (order_id)
        REFERENCES shop_order (order_id) ON DELETE CASCADE,
    CONSTRAINT CK_order_platform_name       CHECK (platform_name IN ('MEITUAN','ELEME')),
    CONSTRAINT CK_order_platform_fee        CHECK (delivery_fee >= 0),
    CONSTRAINT CK_order_platform_rate       CHECK (commission_rate >= 0 AND commission_rate <= 1),
    CONSTRAINT CK_order_platform_amount     CHECK (commission_amount >= 0)
);
GO

-- 批次 6-3　inv_restock_item　补货明细（一行 = 该次补货的一种原料及数量）
CREATE TABLE inv_restock_item (
    restock_id   CHAR(8)        NOT NULL,
    material_id  CHAR(8)        NOT NULL,
    qty          DECIMAL(10,2)  NOT NULL,                       -- 补货数量

    CONSTRAINT PK_inv_restock_item            PRIMARY KEY (restock_id, material_id),
    CONSTRAINT FK_inv_restock_item_restock    FOREIGN KEY (restock_id)  REFERENCES inv_restock (restock_id),
    CONSTRAINT FK_inv_restock_item_material   FOREIGN KEY (material_id) REFERENCES item_material (material_id),
    CONSTRAINT CK_inv_restock_item_qty        CHECK (qty > 0)
);
GO

-- ------------------------------------------------------------
-- 批次 7　inv_stock_log　库存流水（一行 = 一次库存变动）
-- 跨批次最多：引用 item_material、shop_order、inv_restock、staff_employee。
-- 主码用 BIGINT IDENTITY 代理键——日志型数据无人读主键，字母前缀反而无意义。
-- ------------------------------------------------------------
CREATE TABLE inv_stock_log (
    stock_log_id  BIGINT IDENTITY(1,1) NOT NULL,                -- 主码（代理键，自动增长）
    material_id   CHAR(8)        NOT NULL,
    log_type      VARCHAR(10)    NOT NULL,                      -- IN/SALE/LOSS/GAIN/SHORT
    qty           DECIMAL(10,2)  NOT NULL,                      -- 绝对值，方向由 log_type 决定
    order_id      CHAR(12)       NULL,                          -- SALE 关联订单
    restock_id    CHAR(8)        NULL,                          -- IN 关联补货单
    employee_id   CHAR(8)        NOT NULL,                      -- 经办
    created_at    DATETIME2(0)   NOT NULL,

    CONSTRAINT PK_inv_stock_log             PRIMARY KEY (stock_log_id),
    CONSTRAINT FK_inv_stock_log_material    FOREIGN KEY (material_id) REFERENCES item_material (material_id),
    CONSTRAINT FK_inv_stock_log_shop_order  FOREIGN KEY (order_id)    REFERENCES shop_order (order_id),
    CONSTRAINT FK_inv_stock_log_restock     FOREIGN KEY (restock_id)  REFERENCES inv_restock (restock_id),
    CONSTRAINT FK_inv_stock_log_employee    FOREIGN KEY (employee_id) REFERENCES staff_employee (employee_id),
    CONSTRAINT CK_inv_stock_log_type        CHECK (log_type IN ('IN','SALE','LOSS','GAIN','SHORT')),
    CONSTRAINT CK_inv_stock_log_qty         CHECK (qty > 0)
);
GO

-- ------------------------------------------------------------
-- 批次 8　member_point_log、member_balance_log
-- 引用 member_member、shop_order。两张表互不依赖。
-- ------------------------------------------------------------

-- 批次 8-1　member_point_log　积分流水（一行 = 一次积分变动）
CREATE TABLE member_point_log (
    point_log_id  BIGINT IDENTITY(1,1) NOT NULL,                -- 主码（代理键）
    member_id     CHAR(8)        NOT NULL,
    point_type    VARCHAR(20)    NOT NULL,                      -- EARN_SALE/EARN_RECHARGE/REVERSAL
    points        INT            NOT NULL,                      -- 绝对值，方向由 point_type
    order_id      CHAR(12)       NULL,                          -- 充值计分为 NULL
    created_at    DATETIME2(0)   NOT NULL,

    CONSTRAINT PK_member_point_log            PRIMARY KEY (point_log_id),
    CONSTRAINT FK_member_point_log_member     FOREIGN KEY (member_id) REFERENCES member_member (member_id),
    CONSTRAINT FK_member_point_log_shop_order FOREIGN KEY (order_id)  REFERENCES shop_order (order_id),
    CONSTRAINT CK_member_point_log_type       CHECK (point_type IN
        ('EARN_SALE','EARN_RECHARGE','REVERSAL')),
    CONSTRAINT CK_member_point_log_points     CHECK (points > 0),
    -- 订单关联与类型互为充要：EARN_RECHARGE（充值送分）无订单，其余类型必有订单。
    -- 依据：docs/02 字段说明已明写"充值计分为 NULL"——映射由文档确定，非本表假设。
    -- 写法同 CK_inv_restock_received：OR 展开双向蕴含。不能用
    --   `(point_type = 'EARN_RECHARGE') = (order_id IS NULL)`——T-SQL 无布尔类型，
    --   谓词不能作 `=` 操作数，实测报错 102（见 docs/06 §六.6）。
    -- 外码列含 NULL 时不检查引用完整性，故 EARN_RECHARGE 行可正常插入。
    CONSTRAINT CK_member_point_log_order      CHECK (
        (point_type  = 'EARN_RECHARGE' AND order_id IS NULL)
     OR (point_type <> 'EARN_RECHARGE' AND order_id IS NOT NULL)
    )
);
GO

-- 批次 8-2　member_balance_log　储值流水（一行 = 一次余额变动）
CREATE TABLE member_balance_log (
    balance_log_id  BIGINT IDENTITY(1,1) NOT NULL,              -- 主码（代理键）
    member_id       CHAR(8)        NOT NULL,
    balance_type    VARCHAR(20)    NOT NULL,                    -- RECHARGE/SPEND/REFUND
    amount          DECIMAL(10,2)  NOT NULL,                    -- 绝对值，方向由 balance_type
    order_id        CHAR(12)       NULL,                        -- 充值为 NULL
    external_ref    VARCHAR(64)    NULL,                        -- 充值外部参考号（幂等键）
    created_at      DATETIME2(0)   NOT NULL,

    CONSTRAINT PK_member_balance_log            PRIMARY KEY (balance_log_id),
    CONSTRAINT FK_member_balance_log_member     FOREIGN KEY (member_id) REFERENCES member_member (member_id),
    CONSTRAINT FK_member_balance_log_shop_order FOREIGN KEY (order_id)  REFERENCES shop_order (order_id),
    CONSTRAINT CK_member_balance_log_type       CHECK (balance_type IN ('RECHARGE','SPEND','REFUND')),
    CONSTRAINT CK_member_balance_log_amount     CHECK (amount > 0)
);
GO

-- 幂等键：external_ref 非空时唯一（仅充值有外部参考号，消费/退回为 NULL）。
-- 同 shop_order.pay_txn_no：用筛选唯一索引，避免 UNIQUE 约束"最多一行 NULL"的限制。
CREATE UNIQUE INDEX UX_member_balance_log_external_ref
    ON member_balance_log (external_ref)
    WHERE external_ref IS NOT NULL;
GO

-- ------------------------------------------------------------
-- 批次 9　mkt_coupon_rule　券规则（一行 = 一种券的定义）
-- 无外码，但必须先于批次 10 的 mkt_member_coupon。
-- ------------------------------------------------------------
CREATE TABLE mkt_coupon_rule (
    coupon_rule_id    CHAR(8)        NOT NULL,                  -- 主码：CP + 6 位序号
    coupon_name       NVARCHAR(100)  NOT NULL,                  -- 券名称
    coupon_type       VARCHAR(20)    NOT NULL,                  -- FULL_REDUCTION（本版仅此一种）
    threshold_amount  DECIMAL(10,2)  NOT NULL,                  -- 满减门槛（满 X）
    discount_amount   DECIMAL(10,2)  NOT NULL,                  -- 优惠额（减 Y）
    valid_from        DATE           NOT NULL,                  -- 有效期起
    valid_to          DATE           NOT NULL,                  -- 有效期止

    CONSTRAINT PK_mkt_coupon_rule            PRIMARY KEY (coupon_rule_id),
    CONSTRAINT CK_mkt_coupon_rule_type       CHECK (coupon_type IN ('FULL_REDUCTION')),
    CONSTRAINT CK_mkt_coupon_rule_threshold  CHECK (threshold_amount > 0),
    CONSTRAINT CK_mkt_coupon_rule_discount   CHECK (discount_amount  > 0),
    CONSTRAINT CK_mkt_coupon_rule_discount_lt CHECK (discount_amount < threshold_amount),
    CONSTRAINT CK_mkt_coupon_rule_valid      CHECK (valid_to >= valid_from)
);
GO

-- ------------------------------------------------------------
-- 批次 10　mkt_member_coupon　会员持券（一行 = 一会员持有的一张券）
-- 最后一个批次：引用批次 9 的 mkt_coupon_rule、批次 1 的 member_member、
-- 批次 3 的 shop_order。
-- ------------------------------------------------------------
CREATE TABLE mkt_member_coupon (
    member_coupon_id  BIGINT IDENTITY(1,1) NOT NULL,            -- 主码（代理键）
    coupon_rule_id    CHAR(8)        NOT NULL,
    member_id         CHAR(8)        NOT NULL,
    coupon_status     VARCHAR(10)    NOT NULL CONSTRAINT DF_mkt_member_coupon_status DEFAULT 'UNUSED',
    used_order_id     CHAR(12)       NULL,                      -- 核销在哪单；未使用为 NULL
    issued_at         DATETIME2(0)   NOT NULL,                  -- 发放时间

    CONSTRAINT PK_mkt_member_coupon             PRIMARY KEY (member_coupon_id),
    CONSTRAINT FK_mkt_member_coupon_rule        FOREIGN KEY (coupon_rule_id)
        REFERENCES mkt_coupon_rule (coupon_rule_id),
    CONSTRAINT FK_mkt_member_coupon_member      FOREIGN KEY (member_id)
        REFERENCES member_member (member_id),
    CONSTRAINT FK_mkt_member_coupon_shop_order  FOREIGN KEY (used_order_id)
        REFERENCES shop_order (order_id),
    CONSTRAINT CK_mkt_member_coupon_status      CHECK (coupon_status IN
        ('UNUSED','LOCKED','USED','RETURNED'))
);
GO

-- ============================================================
-- 全库自检：17 张表、各类约束总数
-- 预期（与 01-schema.sql 正文逐条对应，任何不符即说明正文有遗漏）：
--   表总数   17
--   主码     17（每表恰 1 个）
--   唯一码    3（item_product(name,size)、item_material(name)、
--              order_platform(platform_order_no)）
--   外码     25（对应 docs/02 §3.1 的 25 条关系，一一对应：第 9 条
--              order_item_addon → order_item 是**一个**覆盖两列的复合外码，
--              不额外多算，故关系数与 FK 数同为 25）
--   检查约束 54
--   默认约束 16
--   筛选唯一索引 3（shop_order.pay_txn_no、member_member.phone、
--              member_balance_log.external_ref）
-- 注 1：sys.key_constraints 同时容纳主码与唯一约束，故其总数 = 17 + 3 = 20。
-- 注 2：**这 3 个候选码不能用 UNIQUE 约束**——SQL Server 的 UNIQUE 把多个 NULL
--       视为相等（全表最多一行 NULL），而这 3 列都允许多行 NULL
--       （现金单无流水号、会员未绑手机、消费无外部参考号）。故改用筛选唯一索引，
--       它们**不计入 sys.key_constraints**，须查 sys.indexes。
-- ============================================================
SELECT
    t.name AS 表名,
    (SELECT COUNT(*) FROM sys.key_constraints     k WHERE k.parent_object_id = t.object_id) AS 键约束数,
    (SELECT COUNT(*) FROM sys.default_constraints d WHERE d.parent_object_id = t.object_id) AS 默认约束数,
    (SELECT COUNT(*) FROM sys.check_constraints   c WHERE c.parent_object_id = t.object_id) AS 检查约束数,
    (SELECT COUNT(*) FROM sys.foreign_keys        f WHERE f.parent_object_id = t.object_id) AS 外码数
FROM sys.tables t
ORDER BY t.name;
GO

SELECT COUNT(*) AS 表总数       FROM sys.tables;
SELECT COUNT(*) AS 外码总数     FROM sys.foreign_keys;
SELECT COUNT(*) AS 检查约束总数 FROM sys.check_constraints;
SELECT COUNT(*) AS 默认约束总数 FROM sys.default_constraints;
SELECT COUNT(*) AS 键约束总数   FROM sys.key_constraints;   -- 应为 20 = 主码 17 + 唯一 3
SELECT SUM(CASE WHEN k.type = 'PK' THEN 1 ELSE 0 END) AS 主码总数,
       SUM(CASE WHEN k.type = 'UQ' THEN 1 ELSE 0 END) AS 唯一约束总数
FROM sys.key_constraints k;
GO

-- 筛选唯一索引（候选码的另一种实现，不在 sys.key_constraints 里）
SELECT i.name AS 索引名, OBJECT_NAME(i.object_id) AS 表名
FROM sys.indexes i
WHERE i.is_unique = 1 AND i.has_filter = 1
ORDER BY 表名;
GO
