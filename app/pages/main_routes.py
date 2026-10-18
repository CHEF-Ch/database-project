"""首页与健康检查。

/health 是 ① 的"运行环境自检"：只读探针，证明"配置 → DAO → SQL Server"
这条纵向链路通了，并且连的是 v1.0（指纹 236）。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template

from app.dao.connection import probe_database
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
    return jsonify(probe_database())
