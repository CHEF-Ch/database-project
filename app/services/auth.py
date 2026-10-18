"""认证：用户存储接口与两种实现。

v1.0 用配置内 demo 账号（ConfigUserStore）；
v2.0 新建 app_user 表后切换为 DbUserStore，业务代码不需要改动。
"""
from __future__ import annotations

from werkzeug.security import check_password_hash

from app.demo_users import DEMO_USERS


class UserStore:
    """凭据校验接口。返回统一结构的用户字典，失败返回 None。"""

    def verify(self, username: str, password: str) -> dict | None:
        raise NotImplementedError

    def list_accounts(self) -> list[dict]:
        """登录页展示用的账号清单（db 存储不应暴露账号列表，返回空）。"""
        return []


class ConfigUserStore(UserStore):
    """v1.0：配置内 demo 账号（app/demo_users.py）。"""

    def __init__(self, users: dict[str, dict] | None = None) -> None:
        self._users = users if users is not None else DEMO_USERS

    def list_accounts(self) -> list[dict]:
        return [
            {"username": name, "role": rec["role"], "display": rec.get("display", name)}
            for name, rec in self._users.items()
        ]

    def verify(self, username: str, password: str) -> dict | None:
        record = self._users.get(username)
        if not record:
            return None
        if not check_password_hash(record["pwd"], password):
            return None
        return {
            "username": username,
            "role": record["role"],
            "display": record.get("display", username),
            "member_id": record.get("member_id"),
        }


class DbUserStore(UserStore):
    """v2.0：从 dbo.app_user 表校验（表尚未创建，此处为接口预留）。"""

    def verify(self, username: str, password: str) -> dict | None:
        from app.dao.connection import db_cursor

        with db_cursor("manager") as cur:
            cur.execute(
                "SELECT password_hash, role_code, member_id, display_name "
                "FROM dbo.app_user WHERE username = ? AND is_active = 1",
                username,
            )
            row = cur.fetchone()

        if row is None or not check_password_hash(row.password_hash, password):
            return None
        return {
            "username": username,
            "role": row.role_code,
            "display": row.display_name or username,
            "member_id": row.member_id,
        }
