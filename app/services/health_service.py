"""健康探针的业务层薄封装。

`/health` 是验收第一个访问的端点，也是"配置 → 数据访问层 → SQL Server"这条
纵向链路的自检口。它原先由页面层直接 `from app.dao.connection import probe_database`，
是整个应用里唯一一处**页面层跨过业务层直连 DAO** 的写路径，与 `docs/09 §2`
"页面层不直连数据库"的口径自相矛盾。

这里补一层无业务规则的转发，让 `/health` 的调用链与其他 24 个路由一致：
    页面层 app/pages/main_routes.py → 业务层 app/services/health_service.py
    → 数据访问层 app/dao/connection.py → SQL Server

注意：本层不做权限判断（`/health` 是公开探针，v1.0 刻意如此，理由见 docs/09 §2 与 §10），
只是把"谁的代码碰 DAO"这件事归位。
"""
from __future__ import annotations

from app.dao.connection import probe_database


def probe() -> dict:
    """返回数据库名/版本/认证方式与结构指纹（只读，无事务）。"""
    return probe_database()
