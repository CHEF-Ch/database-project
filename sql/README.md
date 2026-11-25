# SQL 脚本说明

> **复现方式：在空数据库中执行。** 本目录脚本的复现标准是——**删掉数据库、按下面的顺序一次性跑完，得到相同的表结构与样例数据**。不是在已有库上"不报错地再跑一遍"。
> 脚本用 T-SQL（含 `GO`），不适用于 MySQL。

| 文件 | 用途 | 可重跑？ |
| --- | --- | --- |
| `00-create.sql` | **删掉同名库再建**（先 `DROP` 后 `CREATE`），使整套脚本从一开始就是空库起点 | 是 |
| `01-schema.sql` | 建表（字段 + 非空 + 默认值） | **否**——要求空库；非空库会报"对象已存在" |
| `02-seed.sql` | 装载虚构样例数据 | **否**——重复插会撞主码 / 候选码 |
| `constraint.sql` | 主码 / 候选码 / 外码 / 检查约束（第 4 周从 `01-schema.sql` 抽出，`ALTER TABLE` 追加） | 否 |
| `view.sql` | 创建视图 | 是（`CREATE OR ALTER`） |
| `role.sql` | 创建角色与演示用户并授权 | 是（幂等） |
| `crud.sql` | 增删改查演示（写入后回滚） | 是 |
| `query.sql` | 多表连接 / 统计查询 | 是 |
| `migration.sql` | 第六周：v0.1 → v1.0 结构迁移（在副本 `MilkTeaShop_v01` 上执行，源库不动） | 否——对已迁移库重跑会报"对象已存在" |
| `regression.sql` | 第六周：迁移后回归（业务基准 / 口径 / 指纹 / 约束反例 / 角色权限） | 是 |
| `app_login.sql` | 第七至九周：在 **v1.0 副本**上建 5 个业务 SQL 登录 + `login_app` 写事务账户 + 新建 `role_customer`（可自助下单）与 `role_app` + 补齐授权缺口 + 统计视图授权 + **把 `role_cashier` 对 `shop_order` 的 UPDATE 收窄为 8 列**（退款列仅店长可写） | 是（幂等） |
| `procedure.sql` / `function.sql` | 第十周：两过程三函数（销售统计、库存调整、补货量、销售汇总、折扣） | 是（`CREATE OR ALTER`） |
| `migration_v2_3_audit.sql` | 第十一周：v2.2 → v2.3，建 `inv_stock_audit`、部署触发器与窗口函数、授权 | 是（幂等，需 v2.2） |
| `trigger.sql` | 第十一周：库存审计触发器 + 订单完成规则触发器 | 是（`CREATE OR ALTER`） |
| `window_query.sql` | 第十一周：三类窗口查询函数（排名 / 累计 / 3 日移动平均）+ 演示查询 | 是（`CREATE OR ALTER`） |

> **`00-create.sql` 为什么必须先删再建**：`01-schema.sql`、`02-seed.sql`、`constraint.sql` **都只能在空库上跑**（表已存在会报错、数据重复会撞约束）。若 `00` 只在库不存在时创建，那么第二次复现就会失败——它必须**每次都把库恢复成空的**。这是"复现"与"重跑不报错"的区别。
> 阶段一（1—4 周）不涉及真实数据，删库重建没有损失；`DROP` 前会先 `SET SINGLE_USER WITH ROLLBACK IMMEDIATE` 断开占用连接。

## 执行顺序（复现：空库起点）

```powershell
# ①—④ 建库→建表→约束→装载：每次复现都完整重跑
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/00-create.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/01-schema.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/constraint.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/02-seed.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/view.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/role.sql
# 以下可重复执行验证：
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/crud.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/query.sql
```

**复现验证 = 把上面整段从头再跑一次**（`00-create.sql` 会先删库，故无需手工清理），比对两次得到的表结构与数据是否一致。不要求"第二次也不报错地跳过建表"——要求"第二次得到同样的库"。

> `-f 65001` 指定 UTF-8 编码，避免中文乱码；实例名（`.\SQLEXPRESS`）按本机环境修改。

> **脚本自带上下文与 SET 选项，命令无需再加 `-d` 或 `-I`**：每个脚本都在开头自己 `USE [MilkTeaShop]`（各脚本是独立一次 sqlcmd 进程，上下文不继承），并显式 `SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;`。
> 后者不是可选项：`shop_order` / `member_member` / `member_balance_log` 三张表的候选码是**带筛选条件的唯一索引**（因为 `UNIQUE` 约束在 SQL Server 里只允许一个 NULL，与 `docs/02` §5.2"可空，多 NULL 不冲突"冲突），而 sqlcmd 默认 `QUOTED_IDENTIFIER = OFF`、SSMS 默认 `ON`——不写死就会"在 SSMS 里能跑、按上面的命令却报 `Msg 1934`"。
> 这一处与另外 4 处缺陷的排查经过见 `docs/06-进度/第三周任务流程与状态.md` §六。

## 阶段二迁移（第六周：v0.1 → v1.0）

迁移不在源库上做：先按上面的顺序从空库复现 v0.1，**备份为副本 `MilkTeaShop_v01`**，再在副本上跑迁移与回归。

```powershell
# 1) 从空库复现 v0.1（00→01→constraint→seed→view→role），再备份为副本 MilkTeaShop_v01
#    （SSMS「任务 → 备份」，或 BACKUP DATABASE [MilkTeaShop] TO DISK = '...bak' 后 RESTORE 为 MilkTeaShop_v01）
# 2) 在副本上迁移：
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/migration.sql -o result/第6周-④迁移结果-20261018.txt
# 3) 迁移后回归：
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/regression.sql -o result/第6周-⑤回归结果-20261018.txt
```

> `migration.sql` / `regression.sql` 各自开头 `USE [MilkTeaShop_v01]`，与 v0.1 的 8 个脚本（`USE [MilkTeaShop]`）分属两个库，互不影响。迁移把结构指纹从 **219 → 236**（+1 列 +15 CHECK +1 筛选唯一索引）。

## 第七至九周：应用库角色与登录

应用连 `MilkTeaShop_v01`（v1.0）。第 4 周的 `role.sql` 只作用于 v0.1（`USE [MilkTeaShop]`），
因此 v1.0 副本上的登录单独放在 `app_login.sql`；两者不互相覆盖。

```powershell
# 可选：开启混合认证（使用受限 SQL 登录时才需要；需管理员）
powershell -ExecutionPolicy Bypass -File scripts\enable-mixed-auth.ps1
# 建登录 / 顾客角色 / 补授权 / 店员 UPDATE 收窄（幂等，可重跑）
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/app_login.sql
```

对应关系：`login_clerk→role_cashier`、`login_customer→role_customer`（新建）、`login_maker→role_maker`、`login_stock→role_stock_keeper`、`login_manager→role_manager`。
示例密码只在脚本里作为演示值；真实环境请改密码并通过 `DB_PWD_LOGIN_*` 环境变量注入。

## 第十周 v2.1 验收库

从仓库根目录运行。该脚本只接受 `MilkTeaShop_week10_` 前缀的新库名，使用种子脚本构建，同步部署 v2.0、v2.1 和待支付示例单的耗用快照。需要本机 SQL Server 上的建库和样例角色配置权限，SQL 认证回归还需启用混合认证。

```powershell
.\scripts\create_week10_acceptance_db.ps1 -Database MilkTeaShop_week10_review
$env:DB_NAME = 'MilkTeaShop_week10_review'
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i scripts/week10_objects_test.sql
python scripts/v2_usage_test.py
python scripts/week10_app_test.py
python scripts/week10_app_test.py --sql-roles
python scripts/permission_matrix_test.py --sql-auth
python scripts/order_pages_test.py
python scripts/order_pages_test.py --sql-auth
```

部署选项、对象职责与历史待支付单的处理见 [功能补全与重构设计](../docs/10-数据库编程/功能补全与重构设计.md)。

更新已有的 v2.1 **副本**时，可重新运行 `sql/migration_v2_0.sql` 更新过程、函数和对象授权，版本仍为 2.1。两个迁移入口均拒绝默认原应用库。

## v2.2 数据库账号

在隔离的 v2.1 副本上执行。迁移创建账号表和只读凭据查询过程，并保留 v2.1 订单快照能力。

```powershell
$env:DB_NAME = 'MilkTeaShop_week10_review'
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i sql/migration_v2_2_app_user.sql
python scripts/seed_app_users.py
python scripts/v2_app_user_test.py
$env:USER_STORE = 'db'
python run.py
```

演示账号导入可选。脚本只复制现有密码哈希，保留已存在账号的密码、角色和停用状态；请用具备写账号表权限的管理连接执行。SQL 认证模式下，服务器使用已配置的 `login_manager` 调用凭据查询过程，普通业务角色无权查询账号表。用户停用后再次登录会被拒绝；已有会话的失效策略由 S02 处理。

## 第十一周 v2.3 审计触发器与窗口查询

在 v2.2 副本上执行 `sql/migration_v2_3_audit.sql`：新建 `inv_stock_audit`（审计与业务流水分离）、部署
`trg_item_material_audit` / `trg_shop_order_complete`、三个窗口查询函数，并授予 `role_app` / `role_stock_keeper`
写审计、`role_manager` / `role_stock_keeper` 读审计。版本标记 **2.3**，健康检查指纹 **271**。

```powershell
$env:DB_NAME = 'MilkTeaShop_week11_acceptance'
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i sql/migration_v2_3_audit.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i scripts/week11_trigger_test.sql -o result/第11周-⑤触发器测试.txt
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i scripts/week11_window_test.sql  -o result/第11周-⑥窗口查询验证.txt
python scripts/week11_app_test.py --write
```

验收库可从种子一键建到 2.3：

```powershell
.\scripts\create_week11_acceptance_db.ps1 -Database MilkTeaShop_week11_acceptance
```

加 `-StopBeforeAudit` 只建到 v2.2，用于复跑 ① 基线核对（`scripts/week11_baseline_check.py`）。

> **手动 DML 的 SET 选项**：`inv_stock_audit` 含 `PERSISTED` 计算列 `change_qty`，`shop_order` 等表含筛选唯一索引。
> 用 sqlcmd 手工改这些表前先 `SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;`，否则报 Msg 1934。
> 应用经 ODBC 连接默认满足该选项；项目内 SQL 脚本已在开头写死。

> **触发器报错的事务行为**：触发器内 `THROW` 会把当前事务标记为不可提交（`XACT_STATE=-1`），由调用方整体回滚。
> 应用 `db_cursor(commit=True)` 会走回滚分支；测试脚本对每个预期失败用例单独开事务并在捕获后 `ROLLBACK`。

## 第十二周递归查询与业务事务

从仓库根目录用 sqlcmd 执行 `sql/migration_v2_4_transactions.sql`，目标为 v2.3 或 v2.4 副本。迁移在同一事务内安装两个过程、授权并写入版本标记，表结构指纹仍为 271。部署失败时 sqlcmd 中止并释放连接，重跑前检查版本和对象。恢复至 v2.3 使用升级前备份。

| 对象 | 输入与结果 | 写入职责 |
| --- | --- | --- |
| `recursive_query.sql` | `@requested` 填分类编号，返回节点、相对层级和路径；不存在编号返回空节点 | 只使用脚本自带分类样例，路径中的编号用于阻断环，递归上限为 100 |
| `usp_complete_order` | 订单号、可选完成时间，返回 `affected` 0 或 1 | 支付已按耗用快照扣料，完成过程写入状态和完成时间。v2.3 触发器验证时间、明细、金额和 SALE 扣料证据 |
| `usp_receive_restock` | 补货单号、验收员工、可选验收时间，返回 `affected` 0 或 1 | 先锁单据并校验待入库明细，再按原料号更新库存、写 IN 流水和审计，最后更新单据状态。相同单据重复验收返回 0 |

独立 SQL 调用由过程提交或回滚。过程临时关闭隐式事务模式以避免重复开启事务，返回后恢复调用方设置。Web 应用传入已有事务时，过程参与调用方事务，异常上抛，由应用整体回滚。两条业务过程分别授权 `role_app` 和 `role_stock_keeper`。旧数据库版本仍使用对应版本原有的写入路径。

`scripts/week12_transaction_test.sql` 在 `MilkTeaShop_week11_week12*` 独立库准备并清理固定夹具。P、Q 初始库存为 10、8，正常补 P 五件。回滚用例先将 Q 置于库存上限，使补货第二笔溢出，核对第一笔库存、流水与审计一起恢复。订单回滚用例在完成状态已写入后抛错，验证调用方回滚。

应用回归使用独立测试库。配置 `DB_NAME` 后运行 `python scripts/order_pages_test.py --sql-auth`。页面回归会写入并消耗种子订单，重复运行前应重新建库。

应用专项另设 `DB_AUTH=sql` 与 `DB_PWD_LOGIN_STOCK`、`DB_PWD_LOGIN_APP`、`DB_PWD_LOGIN_MANAGER`，再运行 `python scripts/week12_app_test.py`。支付反例在独立库临时清除最后一种原料的预占，验证较早原料的扣料、流水和审计回滚，随后恢复预占并取消测试单。

## v2.5 跨表规则和用券历史

在 v2.4 副本执行 `sql/migration_v2_5_integrity.sql`。迁移可重复运行，安装事务中检查已有加料配置、
部署五个触发器、建立用券事件表并授予经理只读权限。若已有品类与加料引用冲突，迁移会整体退出，需核对主数据后重跑。
迁移前备份可用于恢复 v2.4。

- 加料选项必须引用加料类型原料，存在选项时不得反向改为普通原料。新增订单行或更改商品、杯型时校验当前规格，新增加料引用须属于该商品。主数据后续变更保留历史行的杯型和加料快照。
- `mkt_coupon_order_event` 随原业务事务记录锁定、核销和归还。迁移只为仍能确认的订单关联记 `BASELINE`，其时间是迁移采集时间。丢失的旧关联保持未知。历史可按旧订单号查询，券再次用于其他订单时仍保留。
- 经理报表读取统计过程、三个窗口函数和临期视图。报价和下单共用商品、加料及券金额校验，报价连接按只读结束，正式提交重新计算并校验库存和券。

在独立库按序部署 v2.3、v2.4、v2.5。以下命令以仓库根目录为工作目录。

```powershell
.\scripts\create_week11_acceptance_db.ps1 -Database MilkTeaShop_week11_refactor
$env:DB_NAME = 'MilkTeaShop_week11_refactor'
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i sql/migration_v2_4_transactions.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i sql/migration_v2_5_integrity.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d $env:DB_NAME -i scripts/v2_5_integrity_test.sql
python scripts/order_pages_test.py --sql-auth
python scripts/v2_5_app_test.py
```

应用专项使用种子账号和有效券，会提交测试订单及用券事件。进行全量复现时使用新建副本。