"""统一错误码与异常处理。

口径（对应任务讲解"安全与稳定"）：
  · 前端只看到错误码 + 面向用户的简明提示；
  · 具体原因（SQL 原文、堆栈、连接信息）只写后端日志，并按请求标识关联。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from flask import Flask, Response, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException


class ErrCode:
    PARAM_INVALID = "E1001"
    AUTH_REQUIRED = "E2001"
    AUTH_FAILED = "E2002"
    FORBIDDEN = "E2003"
    NOT_FOUND = "E3001"
    METHOD_NOT_ALLOWED = "E3002"
    BUSINESS_RULE = "E4001"
    DB_UNAVAILABLE = "E5001"
    DB_CONSTRAINT = "E5002"
    TX_FAILED = "E5003"
    INTERNAL = "E9001"


# 面向用户的提示：不包含任何实现细节
USER_MESSAGES: dict[str, str] = {
    ErrCode.PARAM_INVALID: "提交的内容有误，请检查后重试。",
    ErrCode.AUTH_REQUIRED: "请先登录。",
    ErrCode.AUTH_FAILED: "用户名或密码错误。",
    ErrCode.FORBIDDEN: "当前角色无权执行该操作。",
    ErrCode.NOT_FOUND: "找不到请求的资源。",
    ErrCode.METHOD_NOT_ALLOWED: "请求方式不受支持。",
    ErrCode.BUSINESS_RULE: "操作不满足业务规则，请检查输入。",
    ErrCode.DB_UNAVAILABLE: "系统暂时无法完成操作，请稍后再试。",
    ErrCode.DB_CONSTRAINT: "数据不满足约束，操作已被拒绝。",
    ErrCode.TX_FAILED: "操作未能完成，已回滚，请重试。",
    ErrCode.INTERNAL: "系统内部错误，请稍后再试。",
}

DEFAULT_STATUS: dict[str, int] = {
    ErrCode.PARAM_INVALID: 400,
    ErrCode.AUTH_REQUIRED: 401,
    ErrCode.AUTH_FAILED: 401,
    ErrCode.FORBIDDEN: 403,
    ErrCode.NOT_FOUND: 404,
    ErrCode.METHOD_NOT_ALLOWED: 405,
    ErrCode.BUSINESS_RULE: 409,
    ErrCode.DB_UNAVAILABLE: 503,
    ErrCode.DB_CONSTRAINT: 409,
    ErrCode.TX_FAILED: 500,
    ErrCode.INTERNAL: 500,
}


@dataclass
class AppError(Exception):
    """业务/系统异常的统一载体。"""

    code: str
    message: str | None = None
    detail: str | None = None          # 仅进日志，不返回前端
    status: int | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        Exception.__init__(self, self.code)
        if self.message is None:
            self.message = USER_MESSAGES.get(self.code, USER_MESSAGES[ErrCode.INTERNAL])
        if self.status is None:
            self.status = DEFAULT_STATUS.get(self.code, 400)

    def to_dict(self) -> dict:
        payload = {"ok": False, "code": self.code, "message": self.message}
        if self.extra:
            payload.update(self.extra)
        return payload


# ---- pyodbc 错误 → 统一错误码 -------------------------------------------------
# 注意：不能只做子串匹配——"262" 是 "2627"/"2628" 的子串，会导致
# 唯一键/主键冲突被误判为"无权限"。这里按括号里的消息号精确匹配：
#   约束类：547 / 2601 / 2627 / 2628 / 515 / 8152 / 8115 / 245 / 241
#   权限类：229 / 230 / 262 / 297
# 另外 SQLSTATE 以 23 开头即"完整性约束违反"。
_MSG_NUM_RE = re.compile(r"\((\d{3,5})\)")
_SQLSTATE_RE = re.compile(r"\[(\d{2}[0-9A-Z]{3})\]")

_DB_CONSTRAINT_NUMS = {"547", "2601", "2627", "2628", "515", "8152", "8115", "245", "241", "8114"}
_DB_FORBIDDEN_NUMS = {"229", "230", "262", "297", "15247"}
_DB_TX_NUMS = {"1205", "1222"}          # 死锁牺牲者 / 锁超时
_DB_UNAVAILABLE_NUMS = {"18456", "4060", "233", "10054", "40613", "53"}
_DB_UNAVAILABLE_STATES = {"08001", "08S01", "HYT00", "HYT01", "IM002", "IM003"}


def classify_db_error(exc: BaseException) -> AppError:
    """把 pyodbc/ODBC 异常归类为统一错误码（明细只进日志）。

    判定顺序：约束类 → 事务类 → 权限类 → 连接类 → 其他（按内部错误处理）。
    约束类必须先判，否则 2627 会被 262 先命中。
    """
    text = str(exc)
    numbers = set(_MSG_NUM_RE.findall(text))
    states = set(_SQLSTATE_RE.findall(text))

    if any(state.startswith("23") for state in states) or (numbers & _DB_CONSTRAINT_NUMS):
        return AppError(ErrCode.DB_CONSTRAINT, detail=text)
    if (numbers & _DB_TX_NUMS) or any(state.startswith("40") for state in states):
        return AppError(ErrCode.TX_FAILED, detail=text)
    if numbers & _DB_FORBIDDEN_NUMS:
        return AppError(ErrCode.FORBIDDEN, detail=text)
    if (numbers & _DB_UNAVAILABLE_NUMS) or (states & _DB_UNAVAILABLE_STATES):
        return AppError(ErrCode.DB_UNAVAILABLE, detail=text)
    # 未知的数据库错误更可能是 schema/代码缺陷，不应伪装成“服务暂不可用”
    return AppError(ErrCode.INTERNAL, detail=text)


def wants_json() -> bool:
    if request.path.startswith("/api/") or request.path == "/health":
        return True
    return request.accept_mimetypes.best == "application/json"


def _render_error(err: AppError) -> Response:
    if wants_json():
        return jsonify(err.to_dict()), err.status
    return (
        render_template(
            "error.html",
            code=err.code,
            message=err.message,
            request_id=getattr(g, "request_id", "-"),
        ),
        err.status,
    )


def register_error_handlers(app: Flask) -> None:
    """注册全局异常处理（页面层不再各自 try/except）。"""

    import pyodbc

    @app.before_request
    def _assign_request_id() -> None:
        g.request_id = uuid.uuid4().hex[:12]

    @app.after_request
    def _log_request(response: Response) -> Response:
        app.logger.info(
            "rid=%s role=%s %s %s -> %s",
            getattr(g, "request_id", "-"),
            session.get("role", "anonymous"),
            request.method,
            request.path,
            response.status_code,
        )
        return response

    @app.errorhandler(AppError)
    def _handle_app_error(err: AppError) -> Response:
        app.logger.warning(
            "rid=%s code=%s status=%s path=%s detail=%s",
            getattr(g, "request_id", "-"),
            err.code,
            err.status,
            request.path,
            err.detail or "-",
        )
        return _render_error(err)

    @app.errorhandler(pyodbc.Error)
    def _handle_db_error(exc: pyodbc.Error) -> Response:
        return _handle_app_error(classify_db_error(exc))

    @app.errorhandler(404)
    def _handle_404(_exc) -> Response:
        return _render_error(AppError(ErrCode.NOT_FOUND))

    @app.errorhandler(405)
    def _handle_405(_exc) -> Response:
        return _render_error(AppError(ErrCode.METHOD_NOT_ALLOWED))

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception) -> Response:
        if isinstance(exc, HTTPException):
            mapping = {
                400: ErrCode.PARAM_INVALID,
                401: ErrCode.AUTH_REQUIRED,
                403: ErrCode.FORBIDDEN,
                404: ErrCode.NOT_FOUND,
                405: ErrCode.METHOD_NOT_ALLOWED,
            }
            return _render_error(AppError(mapping.get(exc.code, ErrCode.INTERNAL)))
        app.logger.exception(
            "rid=%s 未处理异常 path=%s", getattr(g, "request_id", "-"), request.path
        )
        return _render_error(AppError(ErrCode.INTERNAL, detail=repr(exc)))
