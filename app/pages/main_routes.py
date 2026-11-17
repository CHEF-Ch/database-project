"""首页与健康检查。

/health 是 ① 的"运行环境自检"：只读探针，证明"配置 → 业务层 → DAO → SQL Server"
这条纵向链路通了，并且连的是 v1.0（指纹 236）。探针逻辑放在业务层
（`app/services/health_service.py`），页面层不直接碰 DAO。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, session
from app.pages._paging import int_arg, page_url
from app.services import member_service
from app.services.permissions import role_required

from app.services.health_service import probe
from app.services.permissions import PERMISSIONS, ROLE_LABELS, current_role

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    role = current_role()
    perms = PERMISSIONS.get(role or "", set())
    return render_template(
        "index.html",
        role=role,
        role_label=ROLE_LABELS.get(role or "", "未登录"),
        permissions=sorted(perms),
        perms=perms,
    )


@bp.get("/health")
def health():
    """只读探针：返回库名、版本、结构指纹。

    不返回服务器地址、账号、密码或任何连接串信息；连接失败由统一异常处理
    转成友好提示（细节只进后端日志）。
    """
    return jsonify(probe())


@bp.get("/account")
@role_required("order.read_own")
def member_account():
    points_offset = int_arg("points_offset", 0, low=0, high=10_000_000)
    balance_offset = int_arg("balance_offset", 0, low=0, high=10_000_000)
    account, points, balance = member_service.get_account(
        session.get("member_id"), points_offset=points_offset, balance_offset=balance_offset,
    )
    return render_template(
        "account.html", account=account, points=points[:20], balance=balance[:20],
        points_prev=page_url("main.member_account", points_offset=max(0, points_offset-20)) if points_offset else None,
        points_next=page_url("main.member_account", points_offset=points_offset+20) if len(points)>20 else None,
        balance_prev=page_url("main.member_account", balance_offset=max(0, balance_offset-20)) if balance_offset else None,
        balance_next=page_url("main.member_account", balance_offset=balance_offset+20) if len(balance)>20 else None,
    )
