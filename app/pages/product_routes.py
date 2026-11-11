"""商品页面/接口（页面层）。

⑤ 落地：列表 / 详情 / 新增 / 编辑。页面层只做接参与渲染，业务校验和写库
交给 ``product_service``；权限由 ``role_required`` 在路由层挡一道，数据库层再兜底。
"""
from __future__ import annotations

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for

from app.errors import AppError, ErrCode
from app.pages._paging import page_url, parse_paging
from app.services import product_service
from app.services.permissions import PERMISSIONS, current_role, role_required

bp = Blueprint("product", __name__, url_prefix="/products")

# 商品是小列表：每页 20 条（超过一页才会出现"下一页"）
PAGE_SIZE = 20

# 可以"就地回填重渲染"的错误：用户/输入层面可修正，重新渲染同一张表单并保留已填内容；
# 其余（403 无权限、500/503 系统故障）仍交给全局异常处理器渲染错误页。
# NOT_FOUND 不在此列：对"商品不存在"回填一张编辑表单没有意义。
_FORM_LOCAL_ERRORS = frozenset(
    {ErrCode.PARAM_INVALID, ErrCode.BUSINESS_RULE, ErrCode.DB_CONSTRAINT}
)


def _perms() -> set[str]:
    """当前角色的权限点集合，供模板做按钮显隐（体验控制）。"""
    return PERMISSIONS.get(current_role() or "", set())


@bp.get("/")
@role_required("product.read")
def list_products():
    q = request.args.get("q") or None
    sale_status = request.args.get("sale_status") or None
    limit, offset = parse_paging(PAGE_SIZE)
    # 多取一行用来判断"还有下一页"，页面上只渲染 limit 行
    rows = product_service.list_products(
        q=q, sale_status=sale_status, limit=limit + 1, offset=offset
    )
    has_next = len(rows) > limit
    return render_template(
        "products/list.html",
        products=rows[:limit],
        q=q or "",
        sale_status=sale_status or "",
        has_next=has_next,
        limit=limit,
        offset=offset,
        prev_url=page_url("product.list_products", offset=max(0, offset - limit)) if offset else None,
        next_url=page_url("product.list_products", offset=offset + limit) if has_next else None,
        perms=_perms(),
    )


@bp.get("/new")
@role_required("product.write")
def new_product():
    return render_template("products/form.html", product=None, perms=_perms())


@bp.post("/")
@role_required("product.write")
def create_product():
    try:
        created = product_service.create_product(
            name=request.form.get("name"),
            size=request.form.get("size"),
            unit_price=request.form.get("unit_price"),
            sale_status=request.form.get("sale_status") or "ON_SALE",
        )
    except AppError as err:
        if err.code not in _FORM_LOCAL_ERRORS:
            raise
        current_app.logger.warning(
            "rid=%s form-error code=%s path=%s detail=%s",
            getattr(g, "request_id", "-"), err.code, request.path, err.detail or "-",
        )
        return (
            render_template(
                "products/form.html",
                product=None,
                form=request.form,
                error=err,
                perms=_perms(),
            ),
            err.status,
        )
    flash(f"商品已保存：{created['product_id']}（{created['name']}）", "success")
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
    try:
        updated = product_service.update_product(product_id, **changes)
    except AppError as err:
        if err.code not in _FORM_LOCAL_ERRORS:
            raise
        current_app.logger.warning(
            "rid=%s form-error code=%s path=%s detail=%s",
            getattr(g, "request_id", "-"), err.code, request.path, err.detail or "-",
        )
        product = product_service.get_product_detail(product_id)
        if product is None:
            raise
        return (
            render_template(
                "products/form.html",
                product=product,
                form=request.form,
                error=err,
                perms=_perms(),
            ),
            err.status,
        )
    flash("商品已更新。", "success")
    return redirect(url_for("product.detail", product_id=updated["product_id"]))
