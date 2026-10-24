"""库存页面/接口（页面层）。

⑤ 落地：原料列表（低库存筛选）、库存调整（GAIN/SHORT 流水）、补货单创建/验收、
库存流水查询。页面层只做接参与渲染，业务校验与写库交给 ``inventory_service``；
权限由 ``role_required`` 挡一道，数据库层再兜底。
"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, session, url_for

from app.services import inventory_service
from app.services.permissions import PERMISSIONS, current_role, role_required

bp = Blueprint("inventory", __name__, url_prefix="/inventory")


def _perms() -> set[str]:
    return PERMISSIONS.get(current_role() or "", set())


@bp.get("/")
@role_required("inventory.read")
def list_stock():
    q = request.args.get("q") or None
    low_stock = bool(request.args.get("low_stock"))
    materials = inventory_service.list_materials(q=q, low_stock=low_stock)
    # 调整表单的下拉框始终给全量原料，避免"仅低库存"筛选时选不到其它原料
    all_materials = materials if not (q or low_stock) else inventory_service.list_materials()
    return render_template(
        "inventory/list.html",
        materials=materials,
        all_materials=all_materials,
        q=q or "",
        low_stock=low_stock,
        perms=_perms(),
    )


@bp.post("/adjust")
@role_required("inventory.adjust")
def adjust_stock():
    inventory_service.adjust_stock(
        material_id=request.form.get("material_id"),
        delta=request.form.get("delta"),
        employee_id=session.get("employee_id"),
    )
    return redirect(url_for("inventory.list_stock"))


@bp.get("/restocks")
@role_required("restock.create")
def restocks():
    restocks = inventory_service.list_restocks()
    all_materials = inventory_service.list_materials()
    return render_template(
        "inventory/restocks.html",
        restocks=restocks,
        all_materials=all_materials,
        perms=_perms(),
    )


@bp.post("/restocks")
@role_required("restock.create")
def create_restock():
    # v1.0 简化：一张补货单只补一种原料；多原料拆成多张单
    inventory_service.create_restock(
        supplier_name=request.form.get("supplier_name"),
        employee_id=session.get("employee_id"),
        items=[(request.form.get("material_id"), request.form.get("qty"))],
    )
    return redirect(url_for("inventory.restocks"))


@bp.post("/restocks/<restock_id>/receive")
@role_required("restock.create")
def receive_restock(restock_id):
    inventory_service.receive_restock(restock_id, employee_id=session.get("employee_id"))
    return redirect(url_for("inventory.restocks"))


@bp.get("/logs")
@role_required("inventory.read")
def stock_logs():
    material_id = request.args.get("material_id") or None
    logs = inventory_service.list_stock_logs(material_id=material_id)
    return render_template(
        "inventory/logs.html",
        logs=logs,
        material_id=material_id or "",
        perms=_perms(),
    )
