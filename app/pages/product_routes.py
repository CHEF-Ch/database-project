"""商品页面/接口（页面层）。

⑤ 落地：列表 / 详情 / 新增 / 编辑。页面层只做接参与渲染，业务校验和写库
交给 ``product_service``；权限由 ``role_required`` 在路由层挡一道，数据库层再兜底。
"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for

from app.errors import AppError, ErrCode
from app.services import product_service
from app.services.permissions import PERMISSIONS, current_role, role_required

bp = Blueprint("product", __name__, url_prefix="/products")


def _perms() -> set[str]:
    """当前角色的权限点集合，供模板做按钮显隐（体验控制）。"""
    return PERMISSIONS.get(current_role() or "", set())


@bp.get("/")
@role_required("product.read")
def list_products():
    q = request.args.get("q") or None
    sale_status = request.args.get("sale_status") or None
    products = product_service.list_products(q=q, sale_status=sale_status)
    return render_template(
        "products/list.html",
        products=products,
        q=q or "",
        sale_status=sale_status or "",
        perms=_perms(),
    )


@bp.get("/new")
@role_required("product.write")
def new_product():
    return render_template("products/form.html", product=None, perms=_perms())


@bp.post("/")
@role_required("product.write")
def create_product():
    created = product_service.create_product(
        name=request.form.get("name"),
        size=request.form.get("size"),
        unit_price=request.form.get("unit_price"),
        sale_status=request.form.get("sale_status") or "ON_SALE",
    )
    return redirect(url_for("product.detail", product_id=created["product_id"]))


@bp.get("/<product_id>")
@role_required("product.read")
def detail(product_id):
    product = product_service.get_product_detail(product_id)
    if product is None:
        raise AppError(ErrCode.NOT_FOUND)
    return render_template("products/detail.html", product=product, perms=_perms())


@bp.get("/<product_id>/edit")
@role_required("product.write")
def edit_product(product_id):
    product = product_service.get_product_detail(product_id)
    if product is None:
        raise AppError(ErrCode.NOT_FOUND)
    return render_template("products/form.html", product=product, perms=_perms())


@bp.post("/<product_id>/edit")
@role_required("product.write")
def update_product(product_id):
    # 只收集表单里实际提交的非空字段，空串按"未提供"处理，避免把空值写进库
    changes = {
        field: request.form.get(field)
        for field in ("name", "size", "unit_price", "sale_status")
        if (request.form.get(field) or "") != ""
    }
    updated = product_service.update_product(product_id, **changes)
    return redirect(url_for("product.detail", product_id=updated["product_id"]))
