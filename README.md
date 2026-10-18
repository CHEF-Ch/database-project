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
│   └── 09-应用开发/              # 第七至九周：应用分层结构、运行与复现
├── app/                       # 第七至九周：Web 应用（分层）
│   ├── pages/                 #   页面层：蓝图/路由，只接参、校验、渲染
│   ├── services/              #   业务层：认证、权限矩阵、业务规则、事务边界
│   ├── dao/                   #   数据访问层：连接/事务管理、参数化 SQL（唯一碰 SQL Server 的层）
│   ├── templates/             #   Jinja2 模板
│   └── static/                #   样式等静态资源
├── scripts/
│   ├── create_v01_copy.sql    # 由 v0.1 源库重建迁移副本（可重复执行，路径不写死）
│   ├── enable-mixed-auth.ps1  # 一次性开启 SQL Server 混合认证（使用受限登录时）
│   ├── smoke_test.py          # ⑦的雏形：骨架用例 + 错误分类 + 真实启动，可写证据
│   └── verify_db_logins.py    # ⑦的雏形：5 个受限登录的数据库级权限正/反例
├── sql/
│   ├── 00-create.sql          # 建库（只创建、不删除已有库）
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
└── result/                    # 实际运行验证证据（sqlcmd 输出 / 自检输出）
```

## 环境与复现

- 数据库：Microsoft SQL Server（建议 2019+，可用 Express）
- 工具：SSMS 或 sqlcmd
- 脚本为 T-SQL（含 `GO`），不适用于 MySQL

复现步骤（从空库开始）：

```powershell
# 实例名按实际环境修改
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/00-create.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/01-schema.sql
# ……按需执行 constraint / seed / view / role / crud / query

# 第二阶段 v1.0 迁移复现（第六周）：先备份 v0.1 为副本 MilkTeaShop_v01，
# 再在副本上执行迁移与回归（源库不动，详见 sql/README.md）
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/migration.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/regression.sql
```

### 第七至九周：Web 应用（v1.0）

应用连接 **`MilkTeaShop_v01`（v1.0，结构指纹 236）**；分层结构、调用链与权限矩阵见
[`docs/09-应用开发/结构与运行.md`](docs/09-应用开发/结构与运行.md)。

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

# 4) 可复跑自检（写 result/ 证据）
python scripts/smoke_test.py --write --real-server
python scripts/verify_db_logins.py --write
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
| 第七至九周：Web 应用（分层、登录、角色权限） | `app/`、`run.py`、`requirements.txt`、`scripts/`、`sql/app_login.sql`、`docs/09-应用开发/` |
| 阶段报告 | `docs/03-阶段报告.md` |
| AI 使用记录 | `docs/04-AI使用记录/`（按成员分子目录） |
| 组内分工表 | `docs/05-组内分工.md` |
