# SQL 脚本说明

> 面向**空库**，按顺序执行；脚本用 T-SQL（含 `GO`），不适用于 MySQL。

| 文件 | 用途 | 可否重跑 |
| --- | --- | --- |
| `00-create.sql` | 创建课程数据库（只创建、不删除已有库） | 是 |
| `01-schema.sql` | 建表，含主码 / 候选码 / 外码 | 否（面向空库） |
| `02-seed.sql` | 装载虚构样例数据 | 否 |
| `constraint.sql` | 添加 CHECK 检查约束 | 否 |
| `view.sql` | 创建视图 | 是（`CREATE OR ALTER`） |
| `role.sql` | 创建角色与演示用户并授权 | 是（幂等） |
| `crud.sql` | 增删改查演示（写入后回滚） | 是 |
| `query.sql` | 多表连接 / 统计查询 | 是 |
| `test.sql` | 断言 + 非法数据 / 越权测试 | 是 |

## 执行顺序

```powershell
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/00-create.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/01-schema.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/constraint.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/02-seed.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/view.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/role.sql
# 以下可重复执行验证：
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/crud.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/query.sql
sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/test.sql
```

> `-f 65001` 指定 UTF-8 编码，避免中文乱码；实例名（`.\SQLEXPRESS`）按本机环境修改。
