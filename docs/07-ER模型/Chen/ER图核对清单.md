# Chen 的 ER 图核对清单

> 第五周「作业一：ER 图」· 陈（成员之一）核对清单。第 2 节链接可编辑 Mermaid 图源。
>
> **事实来源（唯一）**：`docs/02-数据字典.md`（§2 关系模式、§5 码口径）；业务规则见 `docs/01` §4；联系与基数见 `docs/07-ER模型/业务规则与映射清单.md`（组内定稿）。
> **本底稿不发明任何新实体/新联系**：17 实体 = 17 张表，20 条联系 = 25 条外码，与映射清单 §1、§2 逐项一致。

---

## 0. 陈的逐项核对清单（AI 产出须逐项核实）

> 用法：画完图后，逐行回查来源，勾掉"已核实"。**凡勾不掉的，要么改图、要么记入组内问题清单**，不得直接交。

### 0.1 实体（17 = 强 7 + 弱 3 + 关联 4 + 流水 3）

| # | 实体（表名） | 分类 | 主码 / 候选码 | 来源（`docs/02`） | 核实 |
| --- | --- | --- | --- | --- | --- |
| 1 | `item_product` 商品 | 强实体 | `product_id`；候选 `(name,size)` | §2.2 表 1 | ☐ |
| 2 | `item_material` 原料/加料 | 强实体 | `material_id`；候选 `name` | §2.2 表 2 | ☐ |
| 3 | `item_recipe` 配方 | 关联实体（M:N） | `(product_id,material_id)` | §2.2 表 3 | ☐ |
| 4 | `item_addon_option` 商品-加料选项 | 关联实体（M:N） | `(product_id,material_id)` | §2.2 表 4 | ☐ |
| 5 | `shop_order` 订单 | 强实体 | `order_id`；候选 `pay_txn_no` | §2.2 表 5 | ☐ |
| 6 | `order_item` 订单明细 | 弱实体（依附订单） | `(order_id,item_line)` | §2.2 表 6 | ☐ |
| 7 | `order_item_addon` 加料明细 | 关联实体（M:N，依附明细） | `(order_id,item_line,material_id)` | §2.2 表 7 | ☐ |
| 8 | `order_platform` 平台订单信息 | 弱实体（1:0..1 依附订单） | `order_id`（兼外码）；候选 `platform_order_no` | §2.2 表 8 | ☐ |
| 9 | `inv_stock_log` 库存流水 | 流水（审计） | `stock_log_id`（代理键） | §2.2 表 9 | ☐ |
| 10 | `inv_restock` 补货单 | 强实体 | `restock_id` | §2.2 表 10 | ☐ |
| 11 | `inv_restock_item` 补货明细 | 弱实体（依附补货单） | `(restock_id,material_id)` | §2.2 表 11 | ☐ |
| 12 | `member_member` 会员 | 强实体 | `member_id`；候选 `phone` | §2.2 表 12 | ☐ |
| 13 | `member_point_log` 积分流水 | 流水（审计） | `point_log_id`（代理键） | §2.2 表 13 | ☐ |
| 14 | `member_balance_log` 储值流水 | 流水（审计） | `balance_log_id`（代理键）；候选 `external_ref` | §2.2 表 14 | ☐ |
| 15 | `mkt_coupon_rule` 券规则 | 强实体 | `coupon_rule_id` | §2.2 表 15 | ☐ |
| 16 | `mkt_member_coupon` 会员持券 | 关联实体（M:N） | `member_coupon_id`（代理键） | §2.2 表 16 | ☐ |
| 17 | `staff_employee` 员工 | 强实体 | `employee_id` | §2.2 表 17 | ☐ |

### 0.2 联系（20 条，R 编号对齐映射清单 §2）

| # | 联系 | 两端基数（min..max） | 对应外码 | 来源（映射清单） | 核实 |
| --- | --- | --- | --- | --- | --- |
| R1 | 商品─配方─原料 | 商品 1..N · 原料 0..N | `item_recipe` 两 FK | §2 商品域 | ☐ |
| R2 | 商品─加料选项─原料 | 商品 0..N · 原料 0..N | `item_addon_option` 两 FK | §2 商品域 | ☐ |
| R3 | 员工─经办─订单 | 员工 1..N · 订单 1..1 | `shop_order.employee_id` | §2 订单域 | ☐ |
| R4 | 会员─下单─订单 | 会员 0..N · 订单 0..1 | `shop_order.member_id` | §2 订单域 | ☐ |
| R5 | 订单─包含─明细 | 订单 1..N · 明细 1..1 | `order_item.order_id` | §2 订单域 | ☐ |
| R6 | 商品─被订购─明细 | 商品 0..N · 明细 1..1 | `order_item.product_id` | §2 订单域 | ☐ |
| R7 | 明细─加料─原料 | 明细 0..N · 原料 0..N | `order_item_addon` 两 FK | §2 订单域 | ☐ |
| R8 | 订单─平台信息 | 订单 0..1 · 平台信息 1..1 | `order_platform.order_id` | §2 订单域 | ☐ |
| R9 | 原料─库存流水 | 原料 0..N · 流水 1..1 | `inv_stock_log.material_id` | §2 库存域 | ☐ |
| R10 | 订单─销售扣料─流水 | 订单 0..N · 流水 0..1 | `inv_stock_log.order_id` | §2 库存域 | ☐ |
| R11 | 补货单─入库─流水 | 补货单 0..N · 流水 0..1 | `inv_stock_log.restock_id` | §2 库存域 | ☐ |
| R12 | 员工─经办─流水 | 员工 1..N · 流水 1..1 | `inv_stock_log.employee_id` | §2 库存域 | ☐ |
| R13 | 员工─经办─补货单 | 员工 1..N · 补货单 1..1 | `inv_restock.employee_id` | §2 库存域 | ☐ |
| R14 | 补货单─补货明细─原料 | 补货单 1..N · 原料 0..N | `inv_restock_item` 两 FK | §2 库存域 | ☐ |
| R15 | 会员─积分流水 | 会员 0..N · 流水 1..1 | `member_point_log.member_id` | §2 会员域 | ☐ |
| R16 | 订单─计分来源─积分流水 | 订单 0..N · 流水 0..1 | `member_point_log.order_id` | §2 会员域 | ☐ |
| R17 | 会员─储值流水 | 会员 0..N · 流水 1..1 | `member_balance_log.member_id` | §2 会员域 | ☐ |
| R18 | 订单─余额变动─储值流水 | 订单 0..N · 流水 0..1 | `member_balance_log.order_id` | §2 会员域 | ☐ |
| R19 | 会员─持券─券规则 | 会员 0..N · 券规则 0..N | `mkt_member_coupon` 两 FK | §2 券域 | ☐ |
| R20 | 订单─核销─持券 | 订单 0..N · 持券 0..1 | `mkt_member_coupon.used_order_id` | §2 券域 | ☐ |

---

## 1. Chen 记法图例

| 图形 / 标注 | 含义 |
| --- | --- |
| 矩形 | 实体；弱实体用双边框 |
| 菱形 | 联系；`R1` 等编号对应映射清单；标「识别」的联系连接弱实体 |
| 连线端 `1..1` / `0..1` / `1..N` / `0..N` | 基数：必选一 / 可选一 / 至少一个 / 可选多个 |

> 采用简化 Chen 记法：实体属性写在矩形内，主码使用下划线；联系属性写在菱形内。关联表作为实体保留，以便与现有关系模式逐项对应。弱实体框内仅列局部辨识属性，完整关系主码见 §0.1；`order_platform` 由订单主码单独识别，没有局部辨识属性。课件中的双线参与约束在本图用 `1..N` / `1..1` 的最小基数表示。

---

## 2. 简化 Chen ER 图

- [ER 图](ER图.md)：可编辑 Mermaid 源码，包含实体、联系、基数和颜色图例。

---

## 3. 实体中文名对照（英文表名 ↔ 业务名 ↔ 分类）

| 英文表名 | 业务名 | 分类 |
| --- | --- | --- |
| `item_product` | 商品 | 强实体 |
| `item_material` | 原料/加料 | 强实体（同实体两种角色） |
| `item_recipe` | 配方 | 关联实体（商品×原料） |
| `item_addon_option` | 商品-加料选项 | 关联实体（商品×原料·加料视角） |
| `shop_order` | 订单 | 强实体 |
| `order_item` | 订单明细 | 弱实体（依附订单） |
| `order_item_addon` | 加料明细 | 关联实体（明细×原料） |
| `order_platform` | 平台订单信息 | 弱实体（1:0..1 依附订单） |
| `inv_stock_log` | 库存流水 | 流水（审计） |
| `inv_restock` | 补货单 | 强实体 |
| `inv_restock_item` | 补货明细 | 弱实体（依附补货单） |
| `member_member` | 会员 | 强实体 |
| `member_point_log` | 积分流水 | 流水（审计） |
| `member_balance_log` | 储值流水 | 流水（审计） |
| `mkt_coupon_rule` | 券规则 | 强实体 |
| `mkt_member_coupon` | 会员持券 | 关联实体（会员×券规则） |
| `staff_employee` | 员工 | 强实体 |

---

## 4. 绘图核对要点（易错点，落图前自查）

1. **弱实体**：`order_item`、`order_platform`、`inv_restock_item` 依附父实体，主码含父实体主码；在图源中以双框标注。
2. **三类流水**（库存/积分/储值）是审计记录，作依附实体处理，**不**当独立业务实体拉一堆联系。
3. **M:N 联系用关联实体表达**：`item_recipe` / `item_addon_option` / `order_item_addon` / `mkt_member_coupon`；补货明细 `inv_restock_item` 依附补货单并关联原料。
4. **`order_item` 同商品可多行**：主码 `(order_id, item_line)`，不默认「同商品一单只一行」。
5. **可选端**：`shop_order.member_id`、`order_platform`（0..1）、三个流水的 `order_id`/`restock_id`、`mkt_member_coupon.used_order_id`；基数以 §2 链接的两版图源为准。
6. **强制端**：所有非空外码（`employee_id` 各处、明细的 `order_id`、流水的 `material_id`/`member_id`）；基数以 §2 链接的两版图源为准。
7. **参与约束与实现方式分家**：图上标的是业务基数；「每商品必有配方」「每订单至少一条明细」这类 min=1 无约束兜底，属人工/业务要求，见映射清单 §4.3。

---

## 5. 图源使用

直接编辑第 2 节链接的两个版本。Markdown 预览工具支持 Mermaid 时会直接显示图；也可以复制任一版本的代码块到 Mermaid Live 预览。
