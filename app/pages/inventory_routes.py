"""库存页面/接口（骨架）。

③ 实现 DAO，④ 实现"调整库存必须同事务写 inv_stock_log（GAIN/SHORT）"的规则。
"""
from __future__ import annotations

from flask import Blueprint, render_template

from app.services.permissions import role_required

bp = Blueprint("inventory", __name__, url_prefix="/inventory")


@bp.get("/")
@role_required("inventory.read")
def list_stock():
    return render_template(
        "placeholder.html",
        title="库存",
        step="③—⑤",
        detail="库存查询与调整（库存管理员可调整，其余角色只读）",
    )
