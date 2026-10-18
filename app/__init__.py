"""Flask 应用工厂：把页面层、业务层、数据访问层装配起来。"""
from __future__ import annotations

import logging
import os

from flask import Flask

from app.config import Config, load_dotenv
from app.errors import register_error_handlers
from app.services.auth import ConfigUserStore, DbUserStore


def create_app() -> Flask:
    load_dotenv()
    Config.validate()          # 枚举值拼错直接报错，不静默降级

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    app.json.ensure_ascii = False

    _configure_logging(app)
    register_error_handlers(app)

    # 用户存储：v1.0 = 配置内 demo 账号；v2.0 = app_user 表（接口不变）
    app.user_store = DbUserStore() if Config.USER_STORE == "db" else ConfigUserStore()

    from app.pages.auth_routes import bp as auth_bp
    from app.pages.inventory_routes import bp as inventory_bp
    from app.pages.main_routes import bp as main_bp
    from app.pages.order_routes import bp as order_bp
    from app.pages.product_routes import bp as product_bp

    for blueprint in (main_bp, auth_bp, product_bp, inventory_bp, order_bp):
        app.register_blueprint(blueprint)

    app.logger.info(
        "应用启动 db=%s auth=%s user_store=%s",
        Config.DB_NAME,
        Config.DB_AUTH,
        Config.USER_STORE,
    )
    return app


def _configure_logging(app: Flask) -> None:
    """日志落盘：便于按请求标识定位（前端不显示细节）。

    多次 create_app() 时 logger 是同一个对象（按名字缓存），
    因此必须去重，否则日志会重复写入。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    app.logger.setLevel(logging.INFO)
    if any(getattr(h, "_pi_app_file", False) for h in app.logger.handlers):
        return

    handler = logging.FileHandler(os.path.join(log_dir, "app.log"), encoding="utf-8")
    handler._pi_app_file = True          # 标记，避免重复添加
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
