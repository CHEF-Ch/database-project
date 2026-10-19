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


@bp.get("/mine")
@role_required("order.read_own")
def my_orders():
    """顾客/店员的"我的订单"。

    ④ 实现时**必须**按 session 里的 member_id 过滤，否则就是全量越权
    （role_customer 对 shop_order 是表级 SELECT，数据库不会拦）。
    """
    return render_template(
        "placeholder.html",
        title="我的订单",
        step="③—⑤",
        detail="按当前账号绑定的会员号过滤（应用层负责行级隔离）",
    )
