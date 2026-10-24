"""库存域业务层：库存查询、调整（GAIN/SHORT 流水）、补货创建与验收。

事务边界说明：
  · 调整库存 + 写 GAIN/SHORT 流水、验收 + 加库存 + 写 IN 流水，均已由 DAO
    （``adjust_stock_with_log`` / ``receive_restock``）在同一游标内闭环；
  · 本层只负责选连接角色（写走 ``stock``）、做基础校验，并把 DAO 的 None / 异常
    翻译成统一错误码（404 / 409 E4001）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.dao import inventory as _inv
from app.dao.connection import db_cursor
from app.errors import AppError, ErrCode
from app.services.permissions import current_role

SUPPLIER_MAX = 100


def _dec(value) -> Decimal:
    """转成 DECIMAL(10,2) 数量；无法解析直接报参数错误。"""
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"数量不合法：{value!r}") from exc


def _non_zero_delta(value) -> Decimal:
    delta = _dec(value)
    if delta == 0:
        raise AppError(ErrCode.PARAM_INVALID, "调整数量不能为 0。")
    return delta


def _positive_qty(value, *, label: str = "数量") -> Decimal:
    qty = _dec(value)
    if qty <= 0:
        raise AppError(ErrCode.PARAM_INVALID, f"{label}必须大于 0。")
    return qty


def _require_employee(employee_id) -> str:
    """写库存流水/补货单必须有经办员工；缺失说明账号未绑定员工，属配置问题。"""
    eid = (employee_id or "").strip()
    if not eid:
        raise AppError(ErrCode.INTERNAL, detail="当前账号未绑定员工编号（employee_id 缺失）")
    return eid


# ---- 读 ----
def list_materials(
    q: str | None = None,
    low_stock: bool = False,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """原料列表（名称搜索 + 低库存过滤 + 分页）。"""
    with db_cursor(current_role()) as cur:
        return _inv.list_materials(cur, q=q, low_stock=low_stock, limit=limit, offset=offset)


def get_material(material_id: str) -> dict | None:
    """取单个原料；不存在返回 None。"""
    with db_cursor(current_role()) as cur:
        return _inv.get_material(cur, material_id)


def list_stock_logs(
    material_id: str | None = None,
    order_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """库存流水列表（按原料/订单过滤，时间倒序）。"""
    with db_cursor(current_role()) as cur:
        return _inv.list_stock_logs(
            cur, material_id=material_id, order_id=order_id, limit=limit, offset=offset
        )


def list_restocks(limit: int | None = None, offset: int | None = None) -> list[dict]:
    """补货单列表（含明细，供验收页展示）。"""
    with db_cursor(current_role()) as cur:
        rows = _inv.list_restocks(cur, limit=limit, offset=offset)
        for r in rows:
            r["items"] = _inv.list_restock_items(cur, r["restock_id"])
        return rows


# ---- 写（stock）----
def adjust_stock(material_id: str, delta, employee_id: str, log_type: str | None = None) -> dict:
    """调整库存并写 GAIN/SHORT 流水（同一事务）。

    ``log_type`` 缺省由 DAO 按正负推导（正→GAIN、负→SHORT）；报损可显式传 "LOSS"
    （此时 delta 必须为负，由 DAO 校验）。减少导致 ``stock_qty`` 为负或低于
    ``reserved_qty`` 时返回 409 E4001。
    """
    material_id = (material_id or "").strip()
    if not material_id:
        raise AppError(ErrCode.PARAM_INVALID, "缺少原料编号。")
    change = _non_zero_delta(delta)
    eid = _require_employee(employee_id)

    with db_cursor("stock", commit=True) as cur:
        if _inv.get_material(cur, material_id) is None:
            raise AppError(ErrCode.NOT_FOUND)
        result = _inv.adjust_stock_with_log(
            cur,
            material_id=material_id,
            delta=change,
            employee_id=eid,
            log_type=log_type,
        )
        if result is None:
            raise AppError(ErrCode.BUSINESS_RULE, "调整后库存不足（低于预占量或为负）。")
        return result


def create_restock(supplier_name: str, employee_id: str, items) -> dict:
    """创建补货单（单头 PENDING + 明细），不写库存流水。

    ``items`` 为 ``(material_id, qty)`` 序列；数量必须为正。
    """
    supplier_name = (supplier_name or "").strip()
    if not supplier_name:
        raise AppError(ErrCode.PARAM_INVALID, "供应商名称不能为空。")
    if len(supplier_name) > SUPPLIER_MAX:
        raise AppError(ErrCode.PARAM_INVALID, "供应商名称过长。")
    eid = _require_employee(employee_id)

    cleaned_items: list[tuple[str, Decimal]] = []
    for item in items:
        material_id, qty = item
        material_id = (material_id or "").strip()
        if not material_id:
            raise AppError(ErrCode.PARAM_INVALID, "补货明细缺少原料编号。")
        cleaned_items.append((material_id, _positive_qty(qty, label="补货数量")))

    if not cleaned_items:
        raise AppError(ErrCode.PARAM_INVALID, "补货单至少需要一条明细。")

    with db_cursor("stock", commit=True) as cur:
        return _inv.create_restock(
            cur,
            supplier_name=supplier_name,
            employee_id=eid,
            items=cleaned_items,
        )


def receive_restock(restock_id: str, employee_id: str) -> dict:
    """验收入库：PENDING → RECEIVED，加库存并写 IN 流水（同一事务）。

    单据不存在 → 404；不是 PENDING（重复验收 / 已取消）→ 409 E4001。
    """
    restock_id = (restock_id or "").strip()
    if not restock_id:
        raise AppError(ErrCode.PARAM_INVALID, "缺少补货单编号。")
    eid = _require_employee(employee_id)

    with db_cursor("stock", commit=True) as cur:
        if _inv.get_restock(cur, restock_id) is None:
            raise AppError(ErrCode.NOT_FOUND)
        result = _inv.receive_restock(cur, restock_id, employee_id=eid)
        if result is None:
            raise AppError(ErrCode.BUSINESS_RULE, "补货单不是待验收状态，无法验收。")
        return result
