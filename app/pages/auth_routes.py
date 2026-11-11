"""登录 / 退出（页面层）。

登录流程：表单基础校验 → 交给 UserStore 校验凭据 → 写入 session。
角色只从服务端 session 取，绝不接受前端传入的 role 参数。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from app.errors import AppError, ErrCode
from app.services.permissions import ROLE_LABELS

bp = Blueprint("auth", __name__)


def safe_next(raw: str | None) -> str | None:
    r"""把 ``?next=`` 收成"站内相对路径"，否则退回首页（防开放重定向）。

    只接受以单个 ``/`` 开头、不带 host/scheme、不含反斜杠与控制字符的路径：
    ``//evil.com``、``/\evil.com``、``https://evil.com`` 一律丢弃。
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return None
    if any(ch.isspace() or ord(ch) < 32 for ch in candidate):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    return candidate


def _render_login(*, error=None, fields=frozenset(), field_errors=None, form=None):
    """登录页渲染：首次进入与"登录失败就地回填"共用。

    ``fields`` 指定要高亮的输入框（``aria-invalid``），``field_errors`` 给字段级提示。
    """
    accounts = [
        {
            "username": item["username"],
            "role": item["role"],
            "label": ROLE_LABELS.get(item["role"], item["role"]),
        }
        for item in current_app.user_store.list_accounts()
    ]
    next_url = safe_next(request.args.get("next") or request.form.get("next"))
    return (
        render_template(
            "login.html",
            accounts=accounts,
            next_url=next_url,
            error=error,
            fields=fields,
            field_errors=field_errors or {},
            form=form,
        ),
        (error.status if error is not None else 200),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return _render_login()

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    # 页面层只做"必填/长度"这类基础校验，业务判断交给服务层。
    # 提示做到字段级：能指出"哪个框有问题"，但不透露账号是否存在（见下面的统一文案）。
    field_errors: dict[str, str] = {}
    if not username:
        field_errors["username"] = "请输入用户名。"
    elif len(username) > 30:
        field_errors["username"] = "用户名最多 30 个字符。"
    if not password:
        field_errors["password"] = "请输入密码。"
    elif len(password) > 128:
        field_errors["password"] = "密码最多 128 个字符。"
    if field_errors:
        err = AppError(ErrCode.PARAM_INVALID, "请按提示补全登录信息。")
        current_app.logger.warning(
            "rid=%s form-error code=%s path=%s fields=%s",
            getattr(g, "request_id", "-"), err.code, request.path, sorted(field_errors),
        )
        return _render_login(
            error=err, fields=frozenset(field_errors), field_errors=field_errors,
            form=request.form,
        )

    user = current_app.user_store.verify(username, password)
    if user is None:
        # 安全取舍：**不区分**"用户不存在"与"密码错误"（防账号枚举），
        # 两种失败回同一个文案、同一组高亮；明细只进日志。见 docs/09 AUTH-03。
        err = AppError(ErrCode.AUTH_FAILED, detail=f"login failed: {username}")
        current_app.logger.warning(
            "rid=%s auth-failed path=%s detail=%s",
            getattr(g, "request_id", "-"), request.path, err.detail,
        )
        return _render_login(
            error=err, fields=frozenset({"username", "password"}), form=request.form
        )

    session.clear()
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["display"] = user["display"]
    session["member_id"] = user.get("member_id")
    session["employee_id"] = user.get("employee_id")
    flash(f"已登录：{user['display']}（{ROLE_LABELS.get(user['role'], user['role'])}）", "success")
    # 会话过期时从受保护页跳过来（?next=），登录成功直接回原页面
    return redirect(safe_next(request.form.get("next")) or url_for("main.index"))


@bp.post("/logout")
def logout():
    """退出登录（POST：改状态的动作必须带 CSRF 令牌，避免 <img src="/logout"> 式强制登出）。"""
    session.clear()
    flash("已退出登录。", "info")
    return redirect(url_for("auth.login"))
