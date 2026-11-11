# 数据库大作业

> 一个真实经营场景贯穿 17 周的关系数据库项目。当前阶段：第二阶段 v1.0（第 5—9 周：ER 模型 → 规范化与迁移 → Web 应用）。

## 项目简介

- **场景**：奶茶店
- **一句话定位**：服务一家校园周边奶茶店，管理现制饮品的**到店点单**与**外卖平台订单**，并提供会员积分/优惠券/储值、原料级库存与加料定制。

## 阶段规划

> 共 17 周、四个阶段；阶段二跨 5 周，其余每阶段 4 周。

| 阶段 | 周次 | 主题 |
| --- | --- | --- |
| 第一阶段 v0.1 | 第 1—4 周 | 数据库初建与 CRUD（业务分析 → 数据字典 → 建库/CRUD → 查询/视图/约束/权限） |
| 第二阶段 v1.0 | 第 5—9 周 | ER 图与范式化 → Web 应用（v1.0） |
| 第三阶段 v2.0 | 第 10—13 周 | 存储过程 / 触发器 / 事务 |
| 第四阶段 v3.0 | 第 14—17 周 | 自然语言转 SQL / 预测 / 补货 |

## 目录结构

```
├── README.md                  # 项目范围、环境、整体链路与复现步骤
├── requirements.txt           # 第七至九周：Web 应用依赖（Flask / pyodbc）
├── run.py                     # 第七至九周：应用启动入口
├── .env.example               # 环境变量示例（复制为 .env；.env 不入库）
├── docs/
│   ├── 01-业务流程与数据边界.md   # 第一周：角色、流程、进库/不进库清单
│   ├── 02-数据字典.md            # 第二周：关系模式、属性域、键、样例元组
│   ├── 03-阶段报告.md            # 阶段报告：设计思路、实验过程、总结
│   ├── 04-AI使用记录/            # AI 使用记录（按成员分子目录，如 Chen的ai使用记录/）
│   ├── 05-组内分工.md            # 成员分工与贡献
│   ├── 06-进度/                  # 按周记录任务流程与完成状态
│   ├── 07-ER模型/                # 第五周：ER 图（每人一份）+ 映射清单、问题清单
│   ├── 08-规范化/                # 第六周：依赖与范式分析、重构方案
│   └── 09-应用开发/              # 第七至九周：应用分层结构、运行与复现、操作手册、测试用例
├── app/                       # 第七至九周：Web 应用（分层）
│   ├── pages/                 #   页面层：蓝图/路由，只接参、校验、渲染
│   ├── services/              #   业务层：认证、权限矩阵、业务规则、事务边界
│   ├── csrf.py                #   安全：CSRF 同步令牌（POST 表单 + 请求头双通道）
│   ├── dao/                   #   数据访问层：连接/事务管理、参数化 SQL（唯一碰 SQL Server 的层）
│   ├── templates/             #   Jinja2 模板
│   └── static/                #   样式等静态资源
├── scripts/
│   ├── week3_review_b.sql     # 第三周③方向B复核：9条新增CHECK + unit 类型（DML 全部 ROLLBACK）
│   ├── week3_seed_check.sql   # 第三周④样例数据复核：17表行数/订单覆盖/快照一致性（只读）
│   ├── create_v01_copy.sql    # 由 v0.1 源库重建迁移副本（可重复执行，路径不写死）
│   ├── enable-mixed-auth.ps1  # 一次性开启 SQL Server 混合认证（使用受限登录时）
│   ├── dao_test.py            # ③ DAO 自检：37 项读写/回滚/幂等/约束分类（事务内回滚）
│   ├── dao_roles_test.py      # ③ DAO 角色自检：DB_AUTH=sql 下 7 条角色路径（含 role_app 两条）
│   ├── order_service_test.py  # ④ 订单业务层自检：29 项下单/支付/取消/退款/状态（事务回滚）
│   ├── order_pages_test.py    # ⑤ 订单页面自检：147 项（含 --sql-auth 受限登录轮）
│   ├── permission_matrix_test.py # ⑥ 权限矩阵：静态收口 4 项 + 行为收口 47 项（拒绝 30 / 放行 17）
│   ├── integration_test.py    # ⑦ 集成测试：132 条用例按 ID 逐条断言（A–J 十组）
│   ├── _csrf_client.py        # 测试侧：客户端自动补 CSRF 令牌（make_client / raw_client）
│   ├── create_test_db.sql     # ⑦ I 组并发沙箱 MilkTeaShop_test（COPY_ONLY 还原，源库只读）
│   ├── smoke_test.py          # ⑦的雏形：骨架用例 + 错误分类 + 真实启动，可写证据
│   └── verify_db_logins.py    # ⑦的雏形：5 个业务登录（不含 login_app）的数据库级权限正/反例
├── sql/
│   ├── 00-create.sql          # 删库重建（先 DROP 后 CREATE），使脚本回到空库起点
│   ├── 01-schema.sql          # 建表（字段/非空/默认值）
│   ├── 02-seed.sql            # 样例数据
│   ├── constraint.sql         # 主码/候选码/外码/检查约束
│   ├── crud.sql               # 增删改查
│   ├── query.sql              # 多表连接查询
│   ├── view.sql               # 统计视图
│   ├── role.sql               # 角色与权限（v0.1）
│   ├── app_login.sql          # 第七至九周：v1.0 上的受限登录 + 顾客角色 + 授权补缺
│   ├── migration.sql          # 第六周：v0.1 → v1.0 结构迁移（副本上执行）
│   └── regression.sql         # 第六周：迁移后回归测试
└── result/                    # 实际运行验证证据（sqlcmd 输出 / 自检输出 / 应用界面截图，索引见 result/README.md）
```

## 设计要点速览（第二阶段 v1.0）

> 本节由任务要求点名要写进 README 的五项缩到一页，详细论证在链接的文件里。数字均与 `result/` 里的证据一致。

| 项 | 结论 | 详见 |
| --- | --- | --- |
| **ER 模型** | 17 实体 / 20 条业务联系（覆盖 25 条外码）/ 4 处 M:N / 3 个弱实体 / 3 类审计流水；每条联系标两端 `min..max` 基数与「约束 / 代码 / 人工」三档实现方式 | `docs/07-ER模型/`（三人各一份 ER 图 + 映射清单 + v0.1 问题清单） |
| **函数依赖与范式** | 1NF、2NF 均 **17/17**；3NF/BCNF **16/17**。唯一严格违例：`shop_order` 的 `{item_subtotal, coupon_discount} → total_amount`（派生列经非主属性传递）；其余“冗余”全是写入时点快照或跨表聚合，不是表内 FD 违例 | `docs/08-规范化/依赖与范式分析.md` §1–§2 |
| **更新异常** | 三类各一例（均用 `02-seed.sql` 真实行）：插入——`item_subtotal=10, total_amount=999` 不报错；修改——`item_subtotal` 34→30 而 `total_amount` 仍 34；删除——删 `PENDING` 单会级联删明细，但 `reserved_qty` 不释放。另 1 处结构缺口 S1：`order_item_addon` 缺 `addon_price` 快照，历史加料金额会随现价漂移 | 同上 §2–§3 |
| **分解方案** | **0 拆 0 并（17 表全保留）**，判据 R1–R6：只有 FD 冗余才拆、快照不拆、1:1 不拆、拆分必须无损连接且依赖保持、能用约束消除异常就不拆。`shop_order` 保留 + 等式 CHECK P12（`total = subtotal − discount`）消除异常；另补 S1 快照列 | `docs/08-规范化/重构方案.md` §1.2–§5 |
| **迁移与回归** | 指纹 **219 → 236**（111 列 + 17 主码 + 3 UQ + 4 筛选唯一索引 + 25 外码 + 60 CHECK + 16 DEFAULT）；迁移 = **+1 列 + 15 CHECK + 1 筛选唯一索引**，只回填 3 行 `addon_price`、行数不变；`regression.sql` 六段核对全过，v0.1 源库保持 219 不动 | `sql/migration.sql`、`sql/regression.sql`、`result/第6周-*` |

应用侧（四层结构、两条调用链、三层权限矩阵、错误码表）见
[`docs/09-应用开发/结构与运行.md`](docs/09-应用开发/结构与运行.md)，逐角色操作步骤见
[`docs/09-应用开发/操作手册.md`](docs/09-应用开发/操作手册.md)。

## 环境与复现

- 数据库：Microsoft SQL Server（建议 2019+，可用 Express）
- 工具：SSMS 或 sqlcmd
- 脚本为 T-SQL（含 `GO`），不适用于 MySQL

复现步骤（从空库开始）：

**(1) 建库链：必备，按顺序执行（跳步会报错）**

```powershell
# 实例名按实际环境修改；这 6 步已由 ⑦ 的 REPRO-01 实测（在空实例上删库重建，6 步 exit=0，
# 结果：17 表 / 110 列 / 45 CHECK / 131 行，与 v0.1 基线一致）
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/00-create.sql        # 删库建库
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/01-schema.sql        # 17 张表（列/非空/默认值）
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/constraint.sql       # 主码/候选码/外码/CHECK
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/02-seed.sql          # 样例数据（17 表 131 行）
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/view.sql             # 4 个统计视图
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/role.sql             # v0.1 角色与权限
```

> 为什么顺序不能换：`constraint.sql` 依赖 `01-schema.sql` 的表，`02-seed.sql` 的数据要能过约束；
> `view.sql` 依赖三张表都已建好。`01-schema.sql` / `constraint.sql` / `02-seed.sql` / `role.sql` 开头均写死 `SET QUOTED_IDENTIFIER ON`
> （否则 sqlcmd 下带筛选唯一索引的三张表会报 Msg 1934，而 SSMS 默认 ON，很容易“在 SSMS 里能跑”但按 README 跑不过）。

**(2) 演示脚本：可选，建库链跑完后随时执行**

```powershell
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/crud.sql            # 增删改查（每个写操作 BEGIN TRAN…ROLLBACK，跑完不污染 seed）
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/query.sql           # 多表/统计查询（只读）
```

**(3) 第二阶段 v1.0 迁移复现（第六周）**：由 v0.1 源库重建副本 `MilkTeaShop_v01`，
再在副本上执行迁移与回归（**源库不动**，详见 `sql/README.md`）

```powershell
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i scripts/create_v01_copy.sql   # v0.1 → 副本
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/migration.sql            # 指纹 219 → 236
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/regression.sql           # 六段回归
```

> `scripts/create_v01_copy.sql` 用实例默认备份/数据目录，换机器不用改；副本已存在会先删除再重建。
> 若你手头已有 `MilkTeaShop_v01`（指纹 236），可跳过这一步直接启动应用。

### 第七至九周：Web 应用（v1.0）

应用连接 **`MilkTeaShop_v01`（v1.0，结构指纹 236）**；分层结构、调用链与权限矩阵见
[`docs/09-应用开发/结构与运行.md`](docs/09-应用开发/结构与运行.md)，逐角色操作步骤见
[`docs/09-应用开发/操作手册.md`](docs/09-应用开发/操作手册.md)。

```powershell
# 1) 可选：开启混合认证并使用受限 SQL 登录（DB_AUTH=trusted 时整步可跳过）
powershell -ExecutionPolicy Bypass -File scripts\enable-mixed-auth.ps1   # 需管理员
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/app_login.sql

# 2) 安装依赖并启动
python -m pip install -r requirements.txt
copy .env.example .env      # 按需修改 SECRET_KEY / DB_AUTH / DB_PWD_*
python run.py               # 默认 http://127.0.0.1:5000

# 3) 自检：应返回 fingerprint.total = 236、match = true
curl http://127.0.0.1:5000/health

# 4) 可复跑自检（写 result/ 证据；全部只读/可回滚，`--concurrency` 那步需先建沙箱库）
python scripts/smoke_test.py --write --real-server                      # 骨架 31 行用例 + 错误分类
python scripts/verify_db_logins.py --write                             # 受限登录 17 项（需 DB_AUTH=sql）
python scripts/dao_test.py                                             # DAO 37 项（事务内回滚）
python scripts/dao_roles_test.py                                       # DAO 角色路径 7/7（需 DB_AUTH=sql）
python scripts/order_service_test.py                                   # 订单业务层 29 项
python scripts/order_pages_test.py --write                             # 订单页面 147 项（另可加 --sql-auth）
python scripts/permission_matrix_test.py --write                       # 权限矩阵 静态 4 + 行为 52
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i scripts/create_test_db.sql   # ⑦ I 组并发沙箱（只需一次）
python scripts/integration_test.py --write --concurrency --real-server --repro   # 132 条用例
```

演示账号（clerk1 / customer1 / maker1 / stock1 / manager1）见结构文档 §7。

## 提交入口

| 材料 | 文件 |
| --- | --- |
| README：范围、环境、链路、复现 | `README.md` |
| 第一周：角色、流程、数据边界 | `docs/01-业务流程与数据边界.md` |
| 第二周：关系模式、域、键、样例 | `docs/02-数据字典.md`、`sql/02-seed.sql` |
| 第三周：建库、CRUD | `sql/00-create.sql`、`sql/01-schema.sql`、`sql/crud.sql` |
| 第四周：查询、视图、约束、权限 | `sql/query.sql`、`sql/view.sql`、`sql/constraint.sql`、`sql/role.sql` |
| 第五周：ER 模型（ER 图、映射清单、问题清单） | `docs/07-ER模型/` |
| 第六周：范式化与迁移 / 回归 | `docs/08-规范化/`、`sql/migration.sql`、`sql/regression.sql` |
| 第七至九周：Web 应用（分层、登录、角色权限） | `app/`、`run.py`、`requirements.txt`、`scripts/`、`sql/app_login.sql`、`docs/09-应用开发/`（结构与运行、操作手册、测试用例） |
| 结果目录（证据与截图） | `result/`（索引：`result/README.md`；界面截图 `result/fig/web-01 … web-15`） |
| 阶段报告 | `docs/03-阶段报告.md` |
| AI 使用记录 | `docs/04-AI使用记录/`（按成员分子目录） |
| 组内分工表 | `docs/05-组内分工.md` |
