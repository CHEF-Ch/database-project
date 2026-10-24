"""登录 / 退出（页面层）。

登录流程：表单基础校验 → 交给 UserStore 校验凭据 → 写入 session。
角色只从服务端 session 取，绝不接受前端传入的 role 参数。
"""
from __future__ import annotations

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from app.errors import AppError, ErrCode
from app.services.permissions import ROLE_LABELS

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        accounts = [
            {
                "username": item["username"],
                "role": item["role"],
                "label": ROLE_LABELS.get(item["role"], item["role"]),
            }
            for item in current_app.user_store.list_accounts()
        ]
        return render_template("login.html", accounts=accounts)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    # 页面层只做"必填/长度"这类基础校验，业务判断交给服务层
    if not username or not password:
        raise AppError(ErrCode.PARAM_INVALID, "请输入用户名和密码。")
    if len(username) > 30 or len(password) > 128:
        raise AppError(ErrCode.PARAM_INVALID)

    user = current_app.user_store.verify(username, password)
    if user is None:
        raise AppError(ErrCode.AUTH_FAILED, detail=f"login failed: {username}")

    session.clear()
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["display"] = user["display"]
    session["member_id"] = user.get("member_id")
    session["employee_id"] = user.get("employee_id")
    return redirect(url_for("main.index"))


@bp.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
