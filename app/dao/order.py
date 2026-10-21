"""订单域 DAO：订单号、订单头/明细/加料、查询、状态与支付/退款字段。

事务边界由业务层控制：下单、支付、取消、退款都应把本模块的多次调用放在
同一个 ``db_cursor(role, commit=True)`` 事务里。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping

from app.dao._utils import (
    fetch_one,
    fetch_scalar,
    normalise_limit,
    normalise_offset,
    rows_to_dicts,
)
from app.errors import AppError, ErrCode

_ORDER_COLUMNS = """
    order_id, channel, order_mode, order_status, member_id, employee_id,
    item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no,
    created_at, paid_at, completed_at, refund_amount, refund_at
"""


def _dec(value: Decimal | int | float | str) -> Decimal:
    """金额/数量统一保留 2 位小数。"""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _rate(value: Decimal | int | float | str) -> Decimal:
    """费率保留 4 位小数（DECIMAL(5,4)）。"""
    return Decimal(str(value)).quantize(Decimal("0.0001"))


def _day_range(cur, day: date | datetime | str | None) -> tuple[str, str]:
    """返回 (当日前缀, 次日前缀)，用于对 CHAR(12) 主码做可 seek 的范围查询。"""
    if day is None:
        cur.execute("SELECT CONVERT(char(8), SYSDATETIME(), 112)")
        prefix = str(fetch_scalar(cur))
    elif isinstance(day, (date, datetime)):
        prefix = day.strftime("%Y%m%d")
    else:
        prefix = str(day)

    if len(prefix) != 8 or not prefix.isdigit():
        raise AppError(ErrCode.PARAM_INVALID, detail="day 必须是 date/datetime 或 YYYYMMDD 字符串")
    try:
        current = datetime.strptime(prefix, "%Y%m%d").date()
    except ValueError as exc:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"非法日期：{prefix!r}") from exc
    return prefix, (current + timedelta(days=1)).strftime("%Y%m%d")


def next_order_id(cur, *, day: date | datetime | str | None = None) -> str:
    """生成订单号：``YYYYMMDD`` + 4 位当日序号。

    同一事务内用 ``UPDLOCK, HOLDLOCK`` 锁住当日订单号范围；并发下单时后到者
    会等待，拿到下一个序号。若仍撞主码，业务层可捕获 2627 重试。
    """
    prefix, next_prefix = _day_range(cur, day)
    cur.execute(
        """
        SELECT ISNULL(MAX(TRY_CAST(RIGHT(order_id, 4) AS int)), 0) + 1
        FROM dbo.shop_order WITH (UPDLOCK, HOLDLOCK)
        WHERE order_id >= CAST(? AS CHAR(12))
          AND order_id <  CAST(? AS CHAR(12))
        """,
        prefix,
        next_prefix,
    )
    sequence = int(fetch_scalar(cur, 1))
    if sequence > 9_999:
        raise AppError(ErrCode.BUSINESS_RULE, detail="当日订单号已超过 4 位")
    return f"{prefix}{sequence:04d}"


def insert_order(
    cur,
    *,
    order_id: str,
    channel: str,
    order_mode: str | None,
    order_status: str,
    member_id: str | None,
    employee_id: str,
    item_subtotal: Decimal | int | float | str,
    coupon_discount: Decimal | int | float | str,
    total_amount: Decimal | int | float | str,
    pay_method: str | None = None,
    pay_txn_no: str | None = None,
    created_at: datetime | None = None,
    paid_at: datetime | None = None,
    completed_at: datetime | None = None,
    refund_amount: Decimal | int | float | str | None = None,
    refund_at: datetime | None = None,
) -> dict[str, Any]:
    """插入订单头并返回插入后的行。"""
    cur.execute(
        """
        INSERT INTO dbo.shop_order
            (order_id, channel, order_mode, order_status, member_id, employee_id,
             item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no,
             created_at, paid_at, completed_at, refund_amount, refund_at)
        OUTPUT INSERTED.order_id, INSERTED.channel, INSERTED.order_mode,
               INSERTED.order_status, INSERTED.member_id, INSERTED.employee_id,
               INSERTED.item_subtotal, INSERTED.coupon_discount, INSERTED.total_amount,
               INSERTED.pay_method, INSERTED.pay_txn_no, INSERTED.created_at,
               INSERTED.paid_at, INSERTED.completed_at, INSERTED.refund_amount,
               INSERTED.refund_at
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        order_id,
        channel,
        order_mode,
        order_status,
        member_id,
        employee_id,
        _dec(item_subtotal),
        _dec(coupon_discount),
        _dec(total_amount),
        pay_method,
        pay_txn_no,
        created_at or datetime.now(),
        paid_at,
        completed_at,
        None if refund_amount is None else _dec(refund_amount),
        refund_at,
    )
    created = fetch_one(cur)
    if created is None:
        raise AppError(ErrCode.INTERNAL, detail="订单插入后未返回行")
    return created


def insert_order_item(
    cur,
    *,
    order_id: str,
    item_line: int,
    product_id: str,
    size: str,
    qty: int,
    unit_price: Decimal | int | float | str,
    sugar_level: str = "FULL",
    ice_level: str = "REGULAR",
    temp_level: str = "COLD",
) -> None:
    """插入一条订单明细（单价为下单时点快照）。"""
    if int(qty) <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="订单明细数量必须大于 0")
    if _dec(unit_price) <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="订单明细单价必须大于 0")
    cur.execute(
        """
        INSERT INTO dbo.order_item
            (order_id, item_line, product_id, size, sugar_level, ice_level,
             temp_level, qty, unit_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        order_id,
        int(item_line),
        product_id,
        size,
        sugar_level,
        ice_level,
        temp_level,
        int(qty),
        _dec(unit_price),
    )


def insert_order_addon(
    cur,
    *,
    order_id: str,
    item_line: int,
    material_id: str,
    qty: int,
    addon_price: Decimal | int | float | str,
) -> None:
    """插入一条加料明细（单价为下单时点快照）。"""
    if int(qty) <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="加料数量必须大于 0")
    if _dec(addon_price) <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="加料单价必须大于 0")
    cur.execute(
        """
        INSERT INTO dbo.order_item_addon
            (order_id, item_line, material_id, qty, addon_price)
        VALUES (?, ?, ?, ?, ?)
        """,
        order_id,
        int(item_line),
        material_id,
        int(qty),
        _dec(addon_price),
    )


def insert_order_platform(
    cur,
    *,
    order_id: str,
    platform_order_no: str,
    platform_name: str,
    delivery_address: str,
    delivery_fee: Decimal | int | float | str,
    commission_rate: Decimal | int | float | str,
    commission_amount: Decimal | int | float | str,
) -> dict[str, Any]:
    """插入平台订单信息（仅平台单）。"""
    rate = _rate(commission_rate)
    if rate < 0 or rate > 1:
        raise AppError(ErrCode.PARAM_INVALID, detail="平台抽成比例必须在 0 到 1 之间")
    if _dec(delivery_fee) < 0 or _dec(commission_amount) < 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="配送费/抽成金额不能为负")
    cur.execute(
        """
        INSERT INTO dbo.order_platform
            (order_id, platform_order_no, platform_name, delivery_address,
             delivery_fee, commission_rate, commission_amount)
        OUTPUT INSERTED.order_id, INSERTED.platform_order_no, INSERTED.platform_name,
               INSERTED.delivery_address, INSERTED.delivery_fee,
               INSERTED.commission_rate, INSERTED.commission_amount
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        order_id,
        platform_order_no,
        platform_name,
        delivery_address,
        _dec(delivery_fee),
        rate,
        _dec(commission_amount),
    )
    created = fetch_one(cur)
    if created is None:
        raise AppError(ErrCode.INTERNAL, detail="平台订单插入后未返回行")
    return created


def get_order(
    cur,
    order_id: str,
    *,
    for_update: bool = False,
    member_id: str | None = None,
) -> dict[str, Any] | None:
    """按主码取订单头；``for_update=True`` 时加行锁。

    ``member_id`` 非空时追加会员条件，供顾客详情页做行级隔离（防 IDOR）。
    """
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    sql = f"SELECT {_ORDER_COLUMNS} FROM dbo.shop_order{lock} WHERE order_id = ?"
    params: list[Any] = [order_id]
    if member_id is not None:
        sql += " AND member_id = ?"
        params.append(member_id)
    cur.execute(sql, *params)
    return fetch_one(cur)


def get_order_platform(cur, order_id: str) -> dict[str, Any] | None:
    """取平台订单信息；非平台单返回 None。"""
    cur.execute(
        """
        SELECT order_id, platform_order_no, platform_name, delivery_address,
               delivery_fee, commission_rate, commission_amount
        FROM dbo.order_platform
        WHERE order_id = ?
        """,
        order_id,
    )
    return fetch_one(cur)


def find_order_by_pay_txn(cur, pay_txn_no: str) -> dict[str, Any] | None:
    """按支付流水号查订单，用于支付回调幂等。"""
    cur.execute(
        f"SELECT {_ORDER_COLUMNS} FROM dbo.shop_order WHERE pay_txn_no = ?",
        pay_txn_no,
    )
    return fetch_one(cur)


def list_orders(
    cur,
    *,
    status: str | None = None,
    channel: str | None = None,
    member_id: str | None = None,
    q: str | None = None,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
    limit: int | None = 100,
    offset: int | None = 0,
) -> list[dict[str, Any]]:
    """订单列表：状态/渠道/会员/订单号/日期范围过滤，按时间倒序分页。"""
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("order_status = ?")
        params.append(status)
    if channel:
        where.append("channel = ?")
        params.append(channel)
    if member_id:
        where.append("member_id = ?")
        params.append(member_id)
    if q:
        where.append("order_id LIKE ?")
        params.append(f"%{q}%")
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        # 传 date 时包含当天：< 次日 0 点；传 datetime 时按原值做 < 上界。
        if isinstance(date_to, date) and not isinstance(date_to, datetime):
            date_to = date_to + timedelta(days=1)
        where.append("created_at < ?")
        params.append(date_to)

    sql = f"SELECT {_ORDER_COLUMNS} FROM dbo.shop_order"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, order_id DESC OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    params.extend([normalise_offset(offset), normalise_limit(limit)])

    cur.execute(sql, *params)
    return rows_to_dicts(cur)


def list_orders_by_member(
    cur,
    member_id: str,
    *,
    limit: int | None = 100,
    offset: int | None = 0,
) -> list[dict[str, Any]]:
    """“我的订单”：强制按会员号过滤，顾客行级隔离的唯一入口。"""
    return list_orders(cur, member_id=member_id, limit=limit, offset=offset)


def get_order_items(cur, order_id: str) -> list[dict[str, Any]]:
    """取订单明细，并把加料明细挂到对应明细行的 ``addons`` 字段。"""
    cur.execute(
        """
        SELECT order_id, item_line, product_id, size, sugar_level, ice_level,
               temp_level, qty, unit_price
        FROM dbo.order_item
        WHERE order_id = ?
        ORDER BY item_line
        """,
        order_id,
    )
    items = rows_to_dicts(cur)

    cur.execute(
        """
        SELECT oia.order_id, oia.item_line, oia.material_id,
               m.name AS material_name, m.unit, oia.qty, oia.addon_price
        FROM dbo.order_item_addon AS oia
        JOIN dbo.item_material AS m ON m.material_id = oia.material_id
        WHERE oia.order_id = ?
        ORDER BY oia.item_line, oia.material_id
        """,
        order_id,
    )
    addons_by_line: dict[int, list[dict[str, Any]]] = {}
    for addon in rows_to_dicts(cur):
        addons_by_line.setdefault(int(addon["item_line"]), []).append(addon)

    for item in items:
        item["addons"] = addons_by_line.get(int(item["item_line"]), [])
    return items


def get_order_detail(cur, order_id: str) -> dict[str, Any] | None:
    """订单头 + 明细 + 平台信息；订单不存在返回 None。

    非平台单不查 ``order_platform``，避免没有该表 SELECT 权限的角色被 229 误伤。
    """
    order = get_order(cur, order_id)
    if order is None:
        return None
    platform = get_order_platform(cur, order_id) if order["channel"] == "PLATFORM" else None
    return {
        "order": order,
        "items": get_order_items(cur, order_id),
        "platform": platform,
    }


def get_order_detail_for_member(
    cur,
    order_id: str,
    member_id: str,
) -> dict[str, Any] | None:
    """顾客详情：订单必须属于该会员，否则返回 None（用于 403/404 判定）。"""
    order = get_order(cur, order_id, member_id=member_id)
    if order is None:
        return None
    platform = get_order_platform(cur, order_id) if order["channel"] == "PLATFORM" else None
    return {
        "order": order,
        "items": get_order_items(cur, order_id),
        "platform": platform,
    }


def update_order_payment(
    cur,
    *,
    order_id: str,
    from_status: str,
    pay_method: str,
    pay_txn_no: str | None = None,
    paid_at: datetime | None = None,
) -> int:
    """支付确认：``PENDING → PAID``，写入支付方式/流水号/支付时间。"""
    cur.execute(
        """
        UPDATE dbo.shop_order
        SET order_status = 'PAID', pay_method = ?, pay_txn_no = ?, paid_at = ?
        WHERE order_id = ? AND order_status = ?
        """,
        pay_method,
        pay_txn_no,
        paid_at or datetime.now(),
        order_id,
        from_status,
    )
    return cur.rowcount


def update_order_status(
    cur,
    *,
    order_id: str,
    from_status: str,
    to_status: str,
    completed_at: datetime | None = None,
) -> int:
    """按“当前状态”条件更新订单状态，返回命中行数。

    例如制作员 ``PAID → MAKING`` 或 ``MAKING → COMPLETED``。
    - 非 COMPLETED：只 SET ``order_status``，避免引用 maker 无权更新的
      ``completed_at`` 列（否则列级权限会报 230）；
    - COMPLETED：同时写 ``completed_at``。
    """
    if to_status == "COMPLETED":
        cur.execute(
            """
            UPDATE dbo.shop_order
            SET order_status = ?, completed_at = ?
            WHERE order_id = ? AND order_status = ?
            """,
            to_status,
            completed_at or datetime.now(),
            order_id,
            from_status,
        )
    else:
        cur.execute(
            """
            UPDATE dbo.shop_order
            SET order_status = ?
            WHERE order_id = ? AND order_status = ?
            """,
            to_status,
            order_id,
            from_status,
        )
    return cur.rowcount


def cancel_order(cur, order_id: str) -> int:
    """未付款取消：仅 ``PENDING → CANCELLED``。"""
    cur.execute(
        """
        UPDATE dbo.shop_order
        SET order_status = 'CANCELLED'
        WHERE order_id = ? AND order_status = 'PENDING'
        """,
        order_id,
    )
    return cur.rowcount


def refund_order(
    cur,
    *,
    order_id: str,
    refund_amount: Decimal | int | float | str,
    refund_at: datetime | None = None,
) -> int:
    """整单退款：PAID/MAKING/COMPLETED → REFUNDED，写入退款额与时间。"""
    amount = _dec(refund_amount)
    if amount <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="退款金额必须大于 0")
    cur.execute(
        """
        UPDATE dbo.shop_order
        SET order_status = 'REFUNDED', refund_amount = ?, refund_at = ?
        WHERE order_id = ? AND order_status IN ('PAID', 'MAKING', 'COMPLETED')
        """,
        amount,
        refund_at or datetime.now(),
        order_id,
    )
    return cur.rowcount


def cancel_order_with_stock(
    cur,
    order_id: str,
    usages: Mapping[str, Decimal | int | float | str],
) -> bool:
    """取消 PENDING 单并释放预占：以状态条件更新作为幂等闸门。

    只有 ``cancel_order`` 命中 1 行才执行释放；重复调用直接返回 False，
    不会二次释放预占。释放失败则抛异常，由业务层整体回滚状态更新。
    """
    if cancel_order(cur, order_id) != 1:
        return False
    from app.dao import inventory  # 延迟导入，避免模块顶层循环依赖

    inventory.release_order_stock(cur, usages)
    return True


def count_orders_for_day(cur, day: date | datetime | str | None = None) -> int:
    """统计某日订单数（订单号前缀），用于自检/报表。"""
    if day is None:
        cur.execute("SELECT CONVERT(char(8), SYSDATETIME(), 112)")
        prefix = str(fetch_scalar(cur))
    prefix, next_prefix = _day_range(cur, day)
    cur.execute(
        """
        SELECT COUNT(*)
        FROM dbo.shop_order
        WHERE order_id >= CAST(? AS CHAR(12))
          AND order_id <  CAST(? AS CHAR(12))
        """,
        prefix,
        next_prefix,
    )
    return int(fetch_scalar(cur, 0))
