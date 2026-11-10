"""测试用 Flask 客户端：自动为写请求带上 CSRF 令牌。

背景：v1.0 于 2026-11-10 接入了同步令牌 CSRF（`app/csrf.py`），所有 POST/PUT/PATCH/DELETE
都要求 session 里的令牌与请求回传值一致。测试脚本里大量裸 `client.post(...)` 若不加令牌
会全部 403，因此统一用本模块的 ``make_client()`` 取代 ``app.test_client()``：

  · ``make_client(app)``：包装客户端，**每次写请求前**把令牌写进 session，
    并通过请求头 ``X-CSRF-Token`` 回传（session 可能被 login/logout 重置，故每次都要确保）；
  · ``raw_client(app)``：**不包装**的原始客户端，用于测「无令牌 / 错令牌应被拒」的反例。

本模块不修改生产代码，只影响测试进程。
"""
from __future__ import annotations

from flask.testing import FlaskClient

TOKEN = "test-csrf-token"
SESSION_KEY = "_csrf_token"
HEADER = "X-CSRF-Token"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CSRFClient(FlaskClient):
    """写请求自动补令牌的测试客户端。"""

    def open(self, *args, **kwargs):          # noqa: A003 - 覆写 werkzeug 接口
        method = (kwargs.get("method") or "GET").upper()
        if method in WRITE_METHODS:
            with self.session_transaction() as sess:
                sess[SESSION_KEY] = TOKEN
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(HEADER, TOKEN)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def make_client(app) -> FlaskClient:
    """返回带自动令牌的测试客户端（替代 ``app.test_client()``）。"""
    app.test_client_class = CSRFClient
    return app.test_client()


def raw_client(app) -> FlaskClient:
    """返回**原始**客户端：不带令牌，用来验证 CSRF 确实拦得住。"""
    return FlaskClient(app)
