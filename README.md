# 数据库大作业

> 一个真实经营场景贯穿 17 周的关系数据库项目。当前阶段：第一阶段 v0.1（第 1—4 周，数据库初建与 CRUD）。

## 项目简介

- **场景**：（待填写，例如 奶茶店 / 小卖部）
- **一句话定位**：（待填写，服务谁、卖什么、覆盖哪些业务）
- **成员**：（待填写，2—3 人）

## 目录结构

```
├── README.md                  # 项目范围、环境、整体链路与复现步骤
├── docs/
│   ├── 01-业务流程与数据边界.md   # 第一周：角色、流程、进库/不进库清单
│   ├── 02-数据字典.md            # 第二周：关系模式、属性域、键、样例元组
│   ├── 03-阶段报告.md            # 阶段报告：设计思路、实验过程、总结
│   ├── 04-AI使用记录.md          # AI 提示词、输出、人工修改与验证证据
│   └── 05-组内分工.md            # 成员分工与贡献
├── sql/
│   ├── 00-create.sql          # 建库（只创建、不删除已有库）
│   ├── 01-schema.sql          # 建表（主码/候选码/外码）
│   ├── 02-seed.sql            # 样例数据
│   ├── constraint.sql         # 检查约束
│   ├── crud.sql               # 增删改查
│   ├── query.sql              # 多表连接查询
│   ├── view.sql               # 统计视图
│   └── role.sql               # 角色与权限
└── evidence/                  # 实际运行验证证据（sqlcmd 输出截图/文本）
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
# ……按需执行 constraint / seed / view / role / crud / query / test
```

## 提交入口

| 材料 | 文件 |
| --- | --- |
| README：范围、环境、链路、复现 | `README.md` |
| 第一周：角色、流程、数据边界 | `docs/01-业务流程与数据边界.md` |
| 第二周：关系模式、域、键、样例 | `docs/02-数据字典.md`、`sql/02-seed.sql` |
| 第三周：建库、CRUD | `sql/00-create.sql`、`sql/01-schema.sql`、`sql/crud.sql` |
| 第四周：查询、视图、约束、权限 | `sql/query.sql`、`sql/view.sql`、`sql/constraint.sql`、`sql/role.sql` |
| 阶段报告 | `docs/03-阶段报告.md` |
| AI 使用记录 | `docs/04-AI使用记录.md` |
| 组内分工表 | `docs/05-组内分工.md` |
