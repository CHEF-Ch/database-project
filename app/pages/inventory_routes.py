"""库存页面/接口（页面层）。

⑤ 落地：原料列表（低库存筛选）、库存调整（GAIN/SHORT 流水）、补货单创建/验收、
库存流水查询。页面层只做接参与渲染，业务校验与写库交给 ``inventory_service``；
权限由 ``role_required`` 挡一道，数据库层再兜底。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from app.errors import AppError, ErrCode
from app.pages._paging import page_url, parse_paging
from app.services import inventory_service
from app.services.permissions import PERMISSIONS, current_role, role_required

bp = Blueprint("inventory", __name__, url_prefix="/inventory")

# 小列表每页 20；流水一笔订单就写 6 条左右，每页给 50
PAGE_SIZE = 20
LOG_PAGE_SIZE = 50

# 可以"就地回填重渲染"的错误（与下单表单同一口径）：
# 用户/输入层面可修正才回填；403 无权限、500/503 系统故障仍走全局错误页。
# NOT_FOUND 不在此列：原料/补货单不存在时回填一张表单没有意义（INV-13/14 仍看错误页）。
_FORM_LOCAL_ERRORS = frozenset(
    {ErrCode.PARAM_INVALID, ErrCode.BUSINESS_RULE, ErrCode.DB_CONSTRAINT}
)

# 类型 → 方向：页面上让用户填"绝对数量"，方向由类型决定（DAO 的契约仍是"带符号 delta"）。
_ADJUST_SIGN = {"GAIN": 1, "SHORT": -1, "LOSS": -1}


def _signed_delta(raw, log_type: str | None):
    """把"类型 + 绝对数量"换成 DAO 要的带符号 delta。

    类型留空时原样返回（走老口径：正数 GAIN、负数 SHORT）。
    非数字也原样返回，交给业务层报 400 —— 页面层不重复一份数值规则。
    """
    sign = _ADJUST_SIGN.get(log_type or "")
    if sign is None:
        return raw
    try:
        return Decimal(str(raw).strip()) * sign
    except (InvalidOperation, ValueError):
        return raw


def _perms() -> set[str]:
    return PERMISSIONS.get(current_role() or "", set())


def _render_stock_page(*, error=None, form=None, status: int = 200):
    """库存页渲染：列表 GET 与「调整库存」失败回填共用（口径只此一份）。"""
    q = request.args.get("q") or None
    low_stock = bool(request.args.get("low_stock"))
    limit, offset = parse_paging(PAGE_SIZE)
    rows = inventory_service.list_materials(
        q=q, low_stock=low_stock, limit=limit + 1, offset=offset
    )
    has_next = len(rows) > limit
    # 调整表单的下拉框必须拿到**全量**原料：既不能在"仅低库存"筛选时只剩筛出来的，
    # 也不能因为列表分页而只剩当前页的原料。
    all_materials = inventory_service.list_materials()
    # 顺手取最近 5 条流水：调整完能原地核对"到底写进去没有"，不用再跳一页。
    # ⚠ 只对**数据库层也允许读流水**的角色查（role_stock_keeper / role_manager）：
    # sql/app_login.sql:213 对 role_cashier 执行了 REVOKE SELECT ON dbo.inv_stock_log，
    # 不加这道判断会在 DB_AUTH=sql 下让店员打开库存页直接 403（权限矩阵用例会抓到）。
    recent_logs: list[dict] = []
    if "inventory.logs.read" in _perms():
        try:
            recent_logs = inventory_service.list_stock_logs(limit=5)
        except AppError as err:
            # 兜底：这一块无论如何不能把主列表打挂（授权再变也只丢这一块）
            current_app.logger.warning(
                "rid=%s 跳过最近流水 code=%s", getattr(g, "request_id", "-"), err.code
            )
            recent_logs = []
    return (
        render_template(
            "inventory/list.html",
            recent_logs=recent_logs,
            materials=rows[:limit],
            all_materials=all_materials,
            q=q or "",
            low_stock=low_stock,
            has_next=has_next,
            limit=limit,
            offset=offset,
            prev_url=page_url("inventory.list_stock", offset=max(0, offset - limit)) if offset else None,
            next_url=page_url("inventory.list_stock", offset=offset + limit) if has_next else None,
            perms=_perms(),
            error=error,
            form=form,
        ),
        status,
    )


@bp.get("/")
@role_required("inventory.read")
def list_stock():
    return _render_stock_page()


@bp.post("/adjust")
@role_required("inventory.adjust")
def adjust_stock():
    log_type = (request.form.get("log_type") or "").strip().upper() or None
    try:
        inventory_service.adjust_stock(
            material_id=request.form.get("material_id"),
            delta=_signed_delta(request.form.get("delta"), log_type),
            employee_id=session.get("employee_id"),
            log_type=log_type,
        )
    except AppError as err:
        if err.code not in _FORM_LOCAL_ERRORS:
            raise
        current_app.logger.warning(
            "rid=%s form-error code=%s path=%s detail=%s",
            getattr(g, "request_id", "-"), err.code, request.path, err.detail or "-",
        )
        return _render_stock_page(error=err, form=request.form, status=err.status)
    # 成功反馈：库存调整是"重复提交会重复扣减"的操作，必须让用户看到已生效
    flash(
        f"库存已更新：{request.form.get('material_id')} 增减 {request.form.get('delta')}"
        f"（流水类型 {log_type or '按正负自动'}），流水已同步写入。",
        "success",
    )
    return redirect(url_for("inventory.list_stock"))


def _render_restocks_page(*, error=None, form=None, status: int = 200):
    """补货单页渲染：列表 GET 与「新建补货单」失败回填共用。"""
    limit, offset = parse_paging(PAGE_SIZE)
    rows = inventory_service.list_restocks(limit=limit + 1, offset=offset)
    has_next = len(rows) > limit
    all_materials = inventory_service.list_materials()
    return (
        render_template(
            "inventory/restocks.html",
            restocks=rows[:limit],
            all_materials=all_materials,
            has_next=has_next,
            limit=limit,
            offset=offset,
            prev_url=page_url("inventory.restocks", offset=max(0, offset - limit)) if offset else None,
            next_url=page_url("inventory.restocks", offset=offset + limit) if has_next else None,
            perms=_perms(),
            error=error,
            form=form,
        ),
        status,
    )


@bp.get("/restocks")
@role_required("restock.create")
def restocks():
    return _render_restocks_page()


@bp.post("/restocks")
@role_required("restock.create")
def create_restock():
    # v1.0 简化：一张补货单只补一种原料；多原料拆成多张单
    try:
        inventory_service.create_restock(
            supplier_name=request.form.get("supplier_name"),
            employee_id=session.get("employee_id"),
            items=[(request.form.get("material_id"), request.form.get("qty"))],
        )
    except AppError as err:
        if err.code not in _FORM_LOCAL_ERRORS:
            raise
        current_app.logger.warning(
            "rid=%s form-error code=%s path=%s detail=%s",
            getattr(g, "request_id", "-"), err.code, request.path, err.detail or "-",
        )
        return _render_restocks_page(error=err, form=request.form, status=err.status)
    flash("补货单已创建（待验收）。", "success")
    return redirect(url_for("inventory.restocks"))


@bp.post("/restocks/<restock_id>/receive")
@role_required("restock.create")
def receive_restock(restock_id):
    inventory_service.receive_restock(restock_id, employee_id=session.get("employee_id"))
    flash(f"补货单 {restock_id} 已验收，库存已入库并写入 IN 流水。", "success")
    return redirect(url_for("inventory.restocks"))


@bp.get("/logs")
@role_required("inventory.logs.read")
def stock_logs():
    material_id = request.args.get("material_id") or None
    limit, offset = parse_paging(LOG_PAGE_SIZE)
    rows = inventory_service.list_stock_logs(
        material_id=material_id, limit=limit + 1, offset=offset
    )
    has_next = len(rows) > limit
    return render_template(
        "inventory/logs.html",
        logs=rows[:limit],
        material_id=material_id or "",
        has_next=has_next,
        limit=limit,
        offset=offset,
        prev_url=page_url("inventory.stock_logs", offset=max(0, offset - limit)) if offset else None,
        next_url=page_url("inventory.stock_logs", offset=offset + limit) if has_next else None,
        perms=_perms(),
    )
