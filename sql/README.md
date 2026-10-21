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
