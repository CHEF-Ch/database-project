"""订单页面/接口（骨架）。

③ 实现 DAO，④ 落实事务边界：订单头 + 明细 + 加料 + 库存扣减/预占同一事务。
"""
from __future__ import annotations

from flask import Blueprint, render_template

from app.services.permissions import role_required

bp = Blueprint("order", __name__, url_prefix="/orders")


@bp.get("/")
@role_required("order.read")
def list_orders():
    return render_template(
        "placeholder.html",
        title="订单",
        step="③—⑤",
        detail="订单创建与查询、订单明细操作（制作员只改状态）",
    )
