"""商品页面/接口（骨架）。

③ 实现 DAO 方法（参数化查询），④ 实现业务规则，⑤ 补全页面。
"""
from __future__ import annotations

from flask import Blueprint, render_template

from app.services.permissions import role_required

bp = Blueprint("product", __name__, url_prefix="/products")


@bp.get("/")
@role_required("product.read")
def list_products():
    return render_template(
        "placeholder.html",
        title="商品",
        step="③—⑤",
        detail="商品查询与维护（店长可新增/修改，店员/顾客只读）",
    )
