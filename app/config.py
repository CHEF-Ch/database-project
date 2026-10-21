"""应用配置：唯一读取环境变量的地方。

页面层、业务层、数据访问层都从这里取配置，不要在别处硬编码连接信息。
"""
from __future__ import annotations

import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOTENV_LOADED = False


def load_dotenv(path: str | None = None) -> None:
    """极简 .env 读取（避免为课设再引入 python-dotenv 依赖）。

    只处理 `KEY=VALUE` 与 `#` 注释；已存在的环境变量优先（不覆盖）。
    默认读取仓库根目录下的 `.env`（不依赖启动时的 cwd）。
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    path = path or os.path.join(_BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    # utf-8-sig：Windows 记事本保存的 UTF-8 带 BOM，用 utf-8 会把 BOM 带到首行键名上，
    # 导致整份 .env 被静默忽略。
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# 关键：必须在 Config 类定义之前加载。
# Config 的属性是在类定义时（import 时）从 os.environ 取值的，
# 若把 load_dotenv() 放在 create_app() 里，.env 永远来不及生效。
load_dotenv()


class Config:
    """运行期配置（全部可由环境变量覆盖）。"""

    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-only-change-me")

    # ---- 数据库连接 ----
    DB_AUTH: str = os.environ.get("DB_AUTH", "trusted").lower()      # trusted | sql
    DB_SERVER: str = os.environ.get("DB_SERVER", r".\SQLEXPRESS")
    DB_NAME: str = os.environ.get("DB_NAME", "MilkTeaShop_v01")
    DB_DRIVER: str = os.environ.get("DB_DRIVER", "ODBC Driver 18 for SQL Server")
    DB_TIMEOUT: int = int(os.environ.get("DB_TIMEOUT", "5"))
    DB_QUERY_TIMEOUT: int = int(os.environ.get("DB_QUERY_TIMEOUT", "15"))

    # ---- 用户存储 ----
    USER_STORE: str = os.environ.get("USER_STORE", "config").lower()  # config | db

    # 应用角色 → 受限数据库登录（DB_AUTH=sql 时生效）
    DB_LOGINS: dict[str, str] = {
        "clerk": "login_clerk",
        "customer": "login_customer",
        "maker": "login_maker",
        "stock": "login_stock",
        "manager": "login_manager",
        "app": "login_app",          # 写事务统一账户（下单/支付/扣料/积分/余额/券）
    }

    @classmethod
    def login_for(cls, role: str) -> str:
        """角色对应的数据库登录名；未知角色直接拒绝（白名单）。"""
        try:
            return cls.DB_LOGINS[role]
        except KeyError as exc:
            raise ValueError(f"未知应用角色：{role!r}") from exc

    @classmethod
    def password_for(cls, login: str) -> str | None:
        """从环境变量取该登录的密码（DB_PWD_<LOGIN>）。"""
        return os.environ.get(f"DB_PWD_{login.upper()}")

    @classmethod
    def validate(cls) -> None:
        """启动时校验关键枚举值，避免拼错后静默降级。"""
        if cls.DB_AUTH not in {"trusted", "sql"}:
            raise ValueError(
                f"DB_AUTH={cls.DB_AUTH!r} 无效，只能是 'trusted' 或 'sql'"
            )
        if cls.USER_STORE not in {"config", "db"}:
            raise ValueError(
                f"USER_STORE={cls.USER_STORE!r} 无效，只能是 'config' 或 'db'"
            )
