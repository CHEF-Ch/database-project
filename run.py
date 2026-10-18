"""启动入口。

    python run.py

环境变量见 .env.example（可复制为 .env）。
"""
from __future__ import annotations

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "5000")),
        debug=os.environ.get("APP_DEBUG", "0") == "1",
    )
