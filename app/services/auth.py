"""认证：用户存储接口与两种实现。

v1.0 用配置内 demo 账号（ConfigUserStore）；
v2.2 使用 app_user，并保留配置账号模式供旧版库使用。
"""
from __future__ import annotations

from werkzeug.security import check_password_hash

from app.dao import user

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
            "employee_id": record.get("employee_id"),
        }


class DbUserStore(UserStore):
    """从数据库读取有效账号及其业务身份。"""

    def verify(self, username: str, password: str) -> dict | None:
        if not username or len(username.encode("utf-16-le")) > 60:
            return None
        record = user.find_active(username)
        if record is None or not check_password_hash(record["password_hash"], password):
            return None
        return {
            "username": record["username"],
            "role": record["role_code"],
            "display": record["display_name"] or record["username"],
            "member_id": record["member_id"],
            "employee_id": record["employee_id"],
        }
