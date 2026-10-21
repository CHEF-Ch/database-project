"""库存域 DAO：原料、库存三动作（预占/确认/释放）、调整流水、补货单。

设计要点：
- 库存数量用条件 UPDATE 完成，条件里直接判断"可售量/预占量"，避免读后写竞态。
- 所有函数接收已打开的 ``cur``，由业务层决定事务边界（下单、验收必须同事务）。
- 流水只写正数绝对值，方向由 ``log_type`` 表达（docs/02 §5.4）。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.dao._utils import (
    fetch_one,
    fetch_scalar,
    normalise_limit,
    normalise_offset,
    placeholders,
    rows_to_dicts,
)
from app.errors import AppError, ErrCode

_MATERIAL_SELECT = """
SELECT material_id, name, unit, material_kind,
       stock_qty, reserved_qty, safety_stock, shelf_life_days,
       (stock_qty - reserved_qty) AS available_qty,
       CASE WHEN stock_qty < safety_stock THEN 1 ELSE 0 END AS is_low
FROM dbo.item_material
"""

_MATERIAL_SELECT_LOCK = """
SELECT material_id, name, unit, material_kind,
       stock_qty, reserved_qty, safety_stock, shelf_life_days,
       (stock_qty - reserved_qty) AS available_qty,
       CASE WHEN stock_qty < safety_stock THEN 1 ELSE 0 END AS is_low
FROM dbo.item_material WITH (UPDLOCK, ROWLOCK)
"""


def _dec(value: Decimal | int | float | str) -> Decimal:
    """金额/数量统一保留 2 位小数，避免 0.005 这类值在 DECIMAL(10,2) 上反复漂移。"""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _positive(value: Decimal | int | float | str, *, label: str = "数量") -> Decimal:
    """校验并返回正数；非正数抛参数错误，避免负数把库存动作反向执行。"""
    result = _dec(value)
    if result <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"{label}必须大于 0")
    return result


def list_materials(
    cur,
    *,
    q: str | None = None,
    low_stock: bool = False,
    limit: int | None = 200,
    offset: int | None = 0,
) -> list[dict[str, Any]]:
    """原料列表；``low_stock=True`` 只返回现存量低于安全库存的原料。"""
    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    if low_stock:
        where.append("stock_qty < safety_stock")

    sql = _MATERIAL_SELECT
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY material_id OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    params.extend([normalise_offset(offset), normalise_limit(limit)])

    cur.execute(sql, *params)
    return rows_to_dicts(cur)


def get_material(cur, material_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    """按主码取原料；``for_update=True`` 时加行锁。"""
    sql = _MATERIAL_SELECT_LOCK if for_update else _MATERIAL_SELECT
    cur.execute(sql + " WHERE material_id = ?", material_id)
    return fetch_one(cur)


def get_materials_for_update(cur, material_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """批量锁原料并按主码返回字典；按主码排序加锁，降低死锁概率。

    下单前先调用一次，确保后续预占/扣减用到的原料都存在且被同一事务锁住。
    """
    ids = sorted(set(material_ids))
    if not ids:
        return {}
    cur.execute(
        _MATERIAL_SELECT_LOCK
        + f" WHERE material_id IN ({placeholders(len(ids))}) ORDER BY material_id",
        *ids,
    )
    return {row["material_id"]: row for row in rows_to_dicts(cur)}


def reserve_stock(cur, material_id: str, qty: Decimal | int | float | str) -> int:
    """预占原料：只加 ``reserved_qty``，不动现存量、不写流水。

    返回 1 表示预占成功，0 表示可售量不足或原料不存在。
    ``qty`` 必须大于 0；负数会抛 E1001，避免把“预占”变成“反向释放”。
    """
    need = _positive(qty)
    cur.execute(
        """
        UPDATE dbo.item_material
        SET reserved_qty = reserved_qty + ?
        WHERE material_id = ?
          AND stock_qty - reserved_qty >= ?
        """,
        need,
        material_id,
        need,
    )
    return cur.rowcount


def release_reserved(cur, material_id: str, qty: Decimal | int | float | str) -> int:
    """释放预占：只减 ``reserved_qty``，不动现存量、不写流水。

    ``qty`` 必须大于 0；负数会抛 E1001，避免把“释放”变成“反向预占”。
    """
    need = _positive(qty)
    cur.execute(
        """
        UPDATE dbo.item_material
        SET reserved_qty = reserved_qty - ?
        WHERE material_id = ?
          AND reserved_qty >= ?
        """,
        need,
        material_id,
        need,
    )
    return cur.rowcount


def confirm_reserved(cur, material_id: str, qty: Decimal | int | float | str) -> int:
    """确认耗用：同时减预占与现存量；返回 1 表示成功。

    ``qty`` 必须大于 0；重复确认的订单级幂等由 ``confirm_order_stock`` 负责。
    """
    need = _positive(qty)
    cur.execute(
        """
        UPDATE dbo.item_material
        SET reserved_qty = reserved_qty - ?,
            stock_qty = stock_qty - ?
        WHERE material_id = ?
          AND reserved_qty >= ?
          AND stock_qty >= ?
        """,
        need,
        need,
        material_id,
        need,
        need,
    )
    return cur.rowcount


def deduct_available(cur, material_id: str, qty: Decimal | int | float | str) -> int:
    """直接扣减可售量（平台单/现金单确认耗用）：不经过预占。

    条件 ``stock_qty - reserved_qty >= qty`` 保证不会挤占其他订单的预占。
    ``qty`` 必须大于 0；平台单的订单级幂等由 ``deduct_order_stock`` 负责。
    """
    need = _positive(qty)
    cur.execute(
        """
        UPDATE dbo.item_material
        SET stock_qty = stock_qty - ?
        WHERE material_id = ?
          AND stock_qty - reserved_qty >= ?
        """,
        need,
        material_id,
        need,
    )
    return cur.rowcount


def adjust_stock(cur, material_id: str, delta: Decimal | int | float | str) -> int:
    """库存调整（正数增加、负数减少），返回 1 表示成功。

    条件保证调整后 ``stock_qty >= 0`` 且 ``stock_qty >= reserved_qty``。
    """
    change = _dec(delta)
    cur.execute(
        """
        UPDATE dbo.item_material
        SET stock_qty = stock_qty + ?
        WHERE material_id = ?
          AND stock_qty + ? >= 0
          AND stock_qty + ? >= reserved_qty
        """,
        change,
        material_id,
        change,
        change,
    )
    return cur.rowcount


def add_stock_log(
    cur,
    *,
    material_id: str,
    log_type: str,
    qty: Decimal | int | float | str,
    employee_id: str,
    order_id: str | None = None,
    restock_id: str | None = None,
    created_at: datetime | None = None,
) -> int:
    """写一条库存流水，返回 ``stock_log_id``。

    v1.0 约束要求：``SALE`` 必带 ``order_id``、``IN`` 必带 ``restock_id``，
    其余类型对应列必须为 NULL；调用方负责传入符合业务语义的组合。
    ``qty`` 必须大于 0（方向由 ``log_type`` 表达）。
    """
    if log_type not in {"IN", "SALE", "LOSS", "GAIN", "SHORT"}:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"未知库存流水类型：{log_type!r}")
    amount = _positive(qty, label="流水数量")
    cur.execute(
        """
        INSERT INTO dbo.inv_stock_log
            (material_id, log_type, qty, order_id, restock_id, employee_id, created_at)
        OUTPUT INSERTED.stock_log_id
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        material_id,
        log_type,
        amount,
        order_id,
        restock_id,
        employee_id,
        created_at or datetime.now(),
    )
    return int(fetch_scalar(cur))


def adjust_stock_with_log(
    cur,
    *,
    material_id: str,
    delta: Decimal | int | float | str,
    employee_id: str,
    log_type: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any] | None:
    """库存调整 + 写流水（同一事务内）。

    返回调整后的原料行；delta 为 0 或调整失败（会破坏约束）时返回 None。
    默认按 delta 正负推导 ``GAIN`` / ``SHORT``；报损可显式传
    ``log_type="LOSS"``（此时 delta 必须为负）。
    """
    change = _dec(delta)
    if change == 0:
        return None

    if log_type is None:
        log_type = "GAIN" if change > 0 else "SHORT"
    if log_type not in {"GAIN", "SHORT", "LOSS"}:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"库存调整流水类型不合法：{log_type!r}")
    if log_type == "LOSS" and change > 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="LOSS 流水的调整量必须为负")
    if log_type == "GAIN" and change < 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="GAIN 流水的调整量必须为正")
    if log_type == "SHORT" and change > 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="SHORT 流水的调整量必须为负")

    if adjust_stock(cur, material_id, change) != 1:
        return None
    add_stock_log(
        cur,
        material_id=material_id,
        log_type=log_type,
        qty=abs(change),
        employee_id=employee_id,
        created_at=created_at,
    )
    return get_material(cur, material_id)


def reserve_order_stock(
    cur,
    usages: Mapping[str, Decimal | int | float | str],
) -> bool:
    """按 ``{material_id: qty}`` 批量预占，按主码排序执行。

    任一原料不足即抛 E4001，由业务层回滚整个事务。
    订单级幂等由“创建订单”的主码/状态守卫负责，本函数不重复预占。
    """
    for material_id in sorted(usages):
        if reserve_stock(cur, material_id, usages[material_id]) != 1:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"原料 {material_id} 可售量不足",
                extra={"material_id": material_id},
            )
    return True


def release_order_stock(
    cur,
    usages: Mapping[str, Decimal | int | float | str],
) -> bool:
    """按 ``{material_id: qty}`` 批量释放预占，按主码排序执行。

    调用方必须先用订单状态条件更新（``cancel_order``）做幂等闸门，
    只有状态确实从 PENDING 变为 CANCELLED 时才调用本函数。
    """
    for material_id in sorted(usages):
        if release_reserved(cur, material_id, usages[material_id]) != 1:
            raise AppError(
                ErrCode.TX_FAILED,
                detail=f"原料 {material_id} 预占量不足以释放",
                extra={"material_id": material_id},
            )
    return True


def confirm_order_stock(
    cur,
    *,
    order_id: str,
    usages: Mapping[str, Decimal | int | float | str],
    employee_id: str,
    created_at: datetime | None = None,
) -> bool:
    """确认订单耗用并写 SALE 流水，以订单+原料的 SALE 流水做幂等键。

    重复调用时，已存在 SALE 流水的原料直接跳过；不会二次扣减库存或重复记账。
    任一原料确认失败即抛 E4001，由业务层回滚。
    """
    for material_id in sorted(usages):
        cur.execute(
            """
            SELECT 1
            FROM dbo.inv_stock_log WITH (UPDLOCK, HOLDLOCK)
            WHERE order_id = ? AND material_id = ? AND log_type = 'SALE'
            """,
            order_id,
            material_id,
        )
        if cur.fetchone() is not None:
            continue
        if confirm_reserved(cur, material_id, usages[material_id]) != 1:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"原料 {material_id} 预占不足，无法确认耗用",
                extra={"material_id": material_id},
            )
        add_stock_log(
            cur,
            material_id=material_id,
            log_type="SALE",
            qty=usages[material_id],
            employee_id=employee_id,
            order_id=order_id,
            created_at=created_at,
        )
    return True


def deduct_order_stock(
    cur,
    *,
    order_id: str,
    usages: Mapping[str, Decimal | int | float | str],
    employee_id: str,
    created_at: datetime | None = None,
) -> bool:
    """直接扣减订单耗用并写 SALE 流水（现金单/平台单），以 SALE 流水做幂等键。"""
    for material_id in sorted(usages):
        cur.execute(
            """
            SELECT 1
            FROM dbo.inv_stock_log WITH (UPDLOCK, HOLDLOCK)
            WHERE order_id = ? AND material_id = ? AND log_type = 'SALE'
            """,
            order_id,
            material_id,
        )
        if cur.fetchone() is not None:
            continue
        if deduct_available(cur, material_id, usages[material_id]) != 1:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"原料 {material_id} 可售量不足，无法扣减",
                extra={"material_id": material_id},
            )
        add_stock_log(
            cur,
            material_id=material_id,
            log_type="SALE",
            qty=usages[material_id],
            employee_id=employee_id,
            order_id=order_id,
            created_at=created_at,
        )
    return True


def list_stock_logs(
    cur,
    *,
    material_id: str | None = None,
    order_id: str | None = None,
    limit: int | None = 100,
    offset: int | None = 0,
) -> list[dict[str, Any]]:
    """库存流水查询（按原料/订单过滤，按时间倒序）。"""
    where: list[str] = []
    params: list[Any] = []
    if material_id:
        where.append("l.material_id = ?")
        params.append(material_id)
    if order_id:
        where.append("l.order_id = ?")
        params.append(order_id)

    sql = """
        SELECT l.stock_log_id, l.material_id, m.name AS material_name,
               l.log_type, l.qty, l.order_id, l.restock_id,
               l.employee_id, l.created_at
        FROM dbo.inv_stock_log AS l
        JOIN dbo.item_material AS m ON m.material_id = l.material_id
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY l.created_at DESC, l.stock_log_id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    params.extend([normalise_offset(offset), normalise_limit(limit)])

    cur.execute(sql, *params)
    return rows_to_dicts(cur)


def next_restock_id(cur) -> str:
    """生成下一个补货单主码：``R`` + 7 位序号（事务内加锁）。"""
    cur.execute(
        "SELECT ISNULL(MAX(TRY_CAST(SUBSTRING(restock_id, 2, 7) AS int)), 0) + 1 "
        "FROM dbo.inv_restock WITH (UPDLOCK, HOLDLOCK)"
    )
    number = int(fetch_scalar(cur, 1))
    if number > 9_999_999:
        raise AppError(ErrCode.BUSINESS_RULE, detail="补货单主码序号已超过 7 位")
    return f"R{number:07d}"


def create_restock(
    cur,
    *,
    supplier_name: str,
    employee_id: str,
    items: Iterable[tuple[str, Decimal | int | float | str]],
    restock_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """创建补货单（单头 PENDING + 明细），不写库存流水。

    ``items`` 为 ``(material_id, qty)`` 序列；数量必须为正。
    """
    restock_id = restock_id or next_restock_id(cur)
    now = created_at or datetime.now()
    cur.execute(
        """
        INSERT INTO dbo.inv_restock
            (restock_id, supplier_name, restock_status, employee_id, created_at, received_at)
        OUTPUT INSERTED.restock_id, INSERTED.supplier_name, INSERTED.restock_status,
               INSERTED.employee_id, INSERTED.created_at, INSERTED.received_at
        VALUES (?, ?, 'PENDING', ?, ?, NULL)
        """,
        restock_id,
        supplier_name,
        employee_id,
        now,
    )
    header = fetch_one(cur)
    if header is None:
        raise AppError(ErrCode.INTERNAL, detail="补货单插入后未返回行")

    for material_id, qty in items:
        add_restock_item(cur, restock_id, material_id, _positive(qty, label="补货数量"))

    header["items"] = list_restock_items(cur, restock_id)
    return header


def add_restock_item(
    cur,
    restock_id: str,
    material_id: str,
    qty: Decimal | int | float | str,
) -> None:
    """给补货单加一条明细。"""
    cur.execute(
        "INSERT INTO dbo.inv_restock_item (restock_id, material_id, qty) VALUES (?, ?, ?)",
        restock_id,
        material_id,
        _positive(qty, label="补货数量"),
    )


def list_restock_items(cur, restock_id: str) -> list[dict[str, Any]]:
    """补货单明细。"""
    cur.execute(
        """
        SELECT i.restock_id, i.material_id, m.name AS material_name, m.unit, i.qty
        FROM dbo.inv_restock_item AS i
        JOIN dbo.item_material AS m ON m.material_id = i.material_id
        WHERE i.restock_id = ?
        ORDER BY i.material_id
        """,
        restock_id,
    )
    return rows_to_dicts(cur)


def get_restock(cur, restock_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    """取补货单头；``for_update=True`` 时加行锁。"""
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    cur.execute(
        f"""
        SELECT restock_id, supplier_name, restock_status, employee_id,
               created_at, received_at
        FROM dbo.inv_restock{lock}
        WHERE restock_id = ?
        """,
        restock_id,
    )
    return fetch_one(cur)


def receive_restock(
    cur,
    restock_id: str,
    *,
    employee_id: str,
    received_at: datetime | None = None,
) -> dict[str, Any] | None:
    """验收入库：PENDING → RECEIVED，原料加库存并写 IN 流水（同一事务）。

    返回验收后的补货单（含明细）；重复验收/单据不存在/不是 PENDING 时返回 None。
    """
    now = received_at or datetime.now()
    header = get_restock(cur, restock_id, for_update=True)
    if header is None or header["restock_status"] != "PENDING":
        return None

    cur.execute(
        """
        UPDATE dbo.inv_restock
        SET restock_status = 'RECEIVED', received_at = ?
        WHERE restock_id = ? AND restock_status = 'PENDING'
        """,
        now,
        restock_id,
    )
    if cur.rowcount != 1:
        return None

    for item in list_restock_items(cur, restock_id):
        if adjust_stock(cur, item["material_id"], item["qty"]) != 1:
            raise AppError(
                ErrCode.TX_FAILED,
                detail=f"验收时原料 {item['material_id']} 库存调整失败",
                extra={"material_id": item["material_id"]},
            )
        add_stock_log(
            cur,
            material_id=item["material_id"],
            log_type="IN",
            qty=item["qty"],
            employee_id=employee_id,
            restock_id=restock_id,
            created_at=now,
        )

    result = get_restock(cur, restock_id)
    assert result is not None
    result["items"] = list_restock_items(cur, restock_id)
    return result
