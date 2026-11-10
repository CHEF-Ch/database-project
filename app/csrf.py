"""CSRF 防护（v1.0 收口，2026-11-10）。

设计取舍：
  · 不引第三方依赖（`requirements.txt` 只有 Flask / pyodbc），用 25 行自研同步令牌；
  · 令牌存在 session 里（随会话 cookie 走 `HttpOnly + SameSite=Lax`），
    由模板隐藏域 `csrf_token` 或请求头 `X-CSRF-Token` 回传；
  · 只对「会改状态」的方法（POST / PUT / PATCH / DELETE）校验，GET/HEAD/OPTIONS 放行；
  · 校验失败统一走 `AppError(E2003)`，由 `register_error_handlers` 转成 403 + 友好提示，
    细节（缺失 / 不匹配）只进日志，不在页面暴露。

为什么不直接用 Flask-WTF：本项目的表单全是原生 `<form>`（没有 WTForms 表单类），
引入 `Flask-WTF` 只为一个 `CSRFProtect` 会多一条依赖与一套配置；而防护面只需要
「会话内令牌 + 常量时间比较」，自研的可审计性反而更好。v2.0 若引入表单类，再换。
"""
from __future__ import annotations

import hmac
import secrets

from flask import Flask, g, request, session

from app.errors import AppError, ErrCode

SESSION_KEY = "_csrf_token"
FORM_FIELD = "csrf_token"
HEADER = "X-CSRF-Token"

PROTECTED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_token() -> str:
    """取当前会话的令牌；没有就生成（模板里通过 ``csrf_token()`` 调用）。"""
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def _supplied_token() -> str:
    return (request.form.get(FORM_FIELD) or request.headers.get(HEADER) or "").strip()


def init_csrf(app: Flask) -> None:
    """注册 `before_request` 校验与模板全局函数。"""

    @app.before_request
    def _verify_csrf() -> None:
        if request.method not in PROTECTED_METHODS:
            return
        expected = session.get(SESSION_KEY)
        supplied = _supplied_token()
        if expected and supplied and hmac.compare_digest(expected, supplied):
            return
        if not expected:
            app.logger.warning(
                "rid=%s csrf 拒绝：会话无令牌 %s %s",
                getattr(g, "request_id", "-"),
                request.method,
                request.path,
            )
            raise AppError(
                ErrCode.FORBIDDEN,
                "页面已失效，请刷新后重新提交。",
                detail="csrf: session 中没有令牌（可能会话被清或跨站提交）",
            )
        app.logger.warning(
            "rid=%s csrf 拒绝：令牌不匹配 %s %s",
            getattr(g, "request_id", "-"),
            request.method,
            request.path,
        )
        raise AppError(ErrCode.FORBIDDEN, "页面已失效，请刷新后重新提交。", detail="csrf: 令牌不匹配")

    @app.context_processor
    def _inject_csrf() -> dict:
        return {"csrf_token": get_token}
