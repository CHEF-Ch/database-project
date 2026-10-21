"""会员域 DAO：会员快照、积分/储值流水、券规则与会员持券。

积分/余额采用"快照 + 流水"双写（docs/02 §5.3）：
- 快照列在 ``member_member``，用条件 UPDATE 保证不透支；
- 每次变动写一条流水，方向由类型表达。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.dao._utils import fetch_one, fetch_scalar, rows_to_dicts
from app.errors import AppError, ErrCode

_MEMBER_COLUMNS = "member_id, phone, name, points, balance, created_at"

_COUPON_SELECT = """
SELECT mc.member_coupon_id, mc.coupon_rule_id, mc.member_id,
       mc.coupon_status, mc.used_order_id, mc.issued_at,
       r.coupon_name, r.coupon_type, r.threshold_amount,
       r.discount_amount, r.valid_from, r.valid_to
FROM dbo.mkt_member_coupon AS mc{lock}
JOIN dbo.mkt_coupon_rule AS r ON r.coupon_rule_id = mc.coupon_rule_id
"""


def _dec(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def get_member(cur, member_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    """按主码取会员；``for_update=True`` 时加行锁（扣余额/积分前使用）。"""
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    cur.execute(
        f"SELECT {_MEMBER_COLUMNS} FROM dbo.member_member{lock} WHERE member_id = ?",
        member_id,
    )
    return fetch_one(cur)


def get_coupon_rule(cur, coupon_rule_id: str) -> dict[str, Any] | None:
    """取券规则。"""
    cur.execute(
        """
        SELECT coupon_rule_id, coupon_name, coupon_type, threshold_amount,
               discount_amount, valid_from, valid_to
        FROM dbo.mkt_coupon_rule
        WHERE coupon_rule_id = ?
        """,
        coupon_rule_id,
    )
    return fetch_one(cur)


def list_member_coupons(
    cur,
    member_id: str,
    *,
    status: str | None = None,
    for_update: bool = False,
) -> list[dict[str, Any]]:
    """会员持券列表；可按状态过滤，``for_update=True`` 时加行锁。"""
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    sql = _COUPON_SELECT.format(lock=lock)
    params: list[Any] = [member_id]
    sql += " WHERE mc.member_id = ?"
    if status:
        sql += " AND mc.coupon_status = ?"
        params.append(status)
    sql += " ORDER BY mc.member_coupon_id"
    cur.execute(sql, *params)
    return rows_to_dicts(cur)


def get_member_coupon(
    cur,
    member_coupon_id: int,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """按代理键取一张会员券（含券规则）。"""
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    sql = _COUPON_SELECT.format(lock=lock)
    sql += " WHERE mc.member_coupon_id = ?"
    cur.execute(sql, member_coupon_id)
    return fetch_one(cur)


def list_usable_coupons(
    cur,
    member_id: str,
    *,
    subtotal: Decimal | int | float | str,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """列出当前可用于某金额的券：未使用、在有效期内、门槛已满足。"""
    day = today or date.today()
    sql = _COUPON_SELECT.format(lock="") + """
        WHERE mc.member_id = ?
          AND mc.coupon_status = 'UNUSED'
          AND r.valid_from <= ?
          AND r.valid_to >= ?
          AND r.threshold_amount <= ?
        ORDER BY r.threshold_amount DESC, mc.member_coupon_id
    """
    cur.execute(sql, member_id, day, day, _dec(subtotal))
    return rows_to_dicts(cur)


def lock_coupon(cur, member_coupon_id: int, member_id: str, order_id: str) -> int:
    """下单锁定券：UNUSED → LOCKED，并写入 ``used_order_id``。

    v1.0 约束要求非 UNUSED 状态必须有 ``used_order_id``，故锁定与订单号同时写入。
    调用前订单行必须已存在（``used_order_id`` 有外码）。
    """
    cur.execute(
        """
        UPDATE dbo.mkt_member_coupon
        SET coupon_status = 'LOCKED', used_order_id = ?
        WHERE member_coupon_id = ? AND member_id = ?
          AND coupon_status = 'UNUSED' AND used_order_id IS NULL
        """,
        order_id,
        member_coupon_id,
        member_id,
    )
    return cur.rowcount


def use_coupon(cur, member_coupon_id: int, member_id: str, order_id: str) -> int:
    """支付成功核销券：UNUSED/LOCKED → USED。"""
    cur.execute(
        """
        UPDATE dbo.mkt_member_coupon
        SET coupon_status = 'USED', used_order_id = ?
        WHERE member_coupon_id = ? AND member_id = ?
          AND (
                (coupon_status = 'UNUSED' AND used_order_id IS NULL)
             OR (coupon_status = 'LOCKED' AND used_order_id = ?)
          )
        """,
        order_id,
        member_coupon_id,
        member_id,
        order_id,
    )
    return cur.rowcount


def return_coupon(cur, member_coupon_id: int, member_id: str, order_id: str) -> int:
    """取消/整单退款退回券：LOCKED/USED → RETURNED。"""
    cur.execute(
        """
        UPDATE dbo.mkt_member_coupon
        SET coupon_status = 'RETURNED', used_order_id = ?
        WHERE member_coupon_id = ? AND member_id = ?
          AND coupon_status IN ('LOCKED', 'USED')
          AND used_order_id = ?
        """,
        order_id,
        member_coupon_id,
        member_id,
        order_id,
    )
    return cur.rowcount


def insert_point_log(
    cur,
    *,
    member_id: str,
    point_type: str,
    points: int,
    order_id: str | None = None,
    created_at: datetime | None = None,
) -> int:
    """写一条积分流水，返回 ``point_log_id``。

    v1.0 约束：``EARN_RECHARGE`` 必须无订单，其余类型必须有订单；
    ``points`` 存正数绝对值，方向由 ``point_type`` 表达。
    """
    if point_type not in {"EARN_SALE", "EARN_RECHARGE", "REVERSAL"}:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"未知积分类型：{point_type!r}")
    if int(points) <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="积分流水数值必须大于 0")
    if point_type == "EARN_RECHARGE" and order_id is not None:
        raise AppError(ErrCode.PARAM_INVALID, detail="充值积分不能关联订单")
    if point_type != "EARN_RECHARGE" and order_id is None:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"{point_type} 积分必须关联订单")
    cur.execute(
        """
        INSERT INTO dbo.member_point_log
            (member_id, point_type, points, order_id, created_at)
        OUTPUT INSERTED.point_log_id
        VALUES (?, ?, ?, ?, ?)
        """,
        member_id,
        point_type,
        int(points),
        order_id,
        created_at or datetime.now(),
    )
    return int(fetch_scalar(cur))


def update_member_points(cur, member_id: str, delta: int) -> int:
    """按增量更新会员积分快照；条件保证不透支。返回命中行数。"""
    cur.execute(
        """
        UPDATE dbo.member_member
        SET points = points + ?
        WHERE member_id = ? AND points + ? >= 0
        """,
        int(delta),
        member_id,
        int(delta),
    )
    return cur.rowcount


def insert_balance_log(
    cur,
    *,
    member_id: str,
    balance_type: str,
    amount: Decimal | int | float | str,
    order_id: str | None = None,
    external_ref: str | None = None,
    created_at: datetime | None = None,
) -> int:
    """写一条储值流水，返回 ``balance_log_id``。

    v1.0 约束：``RECHARGE`` 必须无订单，``SPEND/REFUND`` 必须有订单；
    ``amount`` 存正数绝对值，方向由 ``balance_type`` 表达。
    """
    if balance_type not in {"RECHARGE", "SPEND", "REFUND"}:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"未知储值类型：{balance_type!r}")
    if _dec(amount) <= 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="储值流水金额必须大于 0")
    if balance_type == "RECHARGE" and order_id is not None:
        raise AppError(ErrCode.PARAM_INVALID, detail="充值流水不能关联订单")
    if balance_type != "RECHARGE" and order_id is None:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"{balance_type} 流水必须关联订单")
    cur.execute(
        """
        INSERT INTO dbo.member_balance_log
            (member_id, balance_type, amount, order_id, external_ref, created_at)
        OUTPUT INSERTED.balance_log_id
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        member_id,
        balance_type,
        _dec(amount),
        order_id,
        external_ref,
        created_at or datetime.now(),
    )
    return int(fetch_scalar(cur))


def update_member_balance(
    cur,
    member_id: str,
    delta: Decimal | int | float | str,
) -> int:
    """按增量更新会员余额快照；条件保证不透支。返回命中行数。"""
    change = _dec(delta)
    cur.execute(
        """
        UPDATE dbo.member_member
        SET balance = balance + ?
        WHERE member_id = ? AND balance + ? >= 0
        """,
        change,
        member_id,
        change,
    )
    return cur.rowcount

def add_member_points(
    cur,
    *,
    member_id: str,
    delta: int,
    point_type: str,
    order_id: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """积分快照 + 流水成对写入（同一事务），返回更新后的会员行。

    快照更新失败（会员不存在或会扣成负数）时抛 E4001。
    """
    if int(delta) == 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="积分变动量不能为 0")
    if update_member_points(cur, member_id, delta) != 1:
        raise AppError(ErrCode.BUSINESS_RULE, detail="会员积分不足或会员不存在")
    insert_point_log(
        cur,
        member_id=member_id,
        point_type=point_type,
        points=abs(int(delta)),
        order_id=order_id,
        created_at=created_at,
    )
    member = get_member(cur, member_id)
    if member is None:
        raise AppError(ErrCode.INTERNAL, detail="会员积分更新后读取失败")
    return member


def add_member_balance(
    cur,
    *,
    member_id: str,
    delta: Decimal | int | float | str,
    balance_type: str,
    order_id: str | None = None,
    external_ref: str | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """余额快照 + 流水成对写入（同一事务），返回更新后的会员行。

    快照更新失败（会员不存在或会扣成负数）时抛 E4001。
    """
    change = _dec(delta)
    if change == 0:
        raise AppError(ErrCode.PARAM_INVALID, detail="余额变动量不能为 0")
    if update_member_balance(cur, member_id, change) != 1:
        raise AppError(ErrCode.BUSINESS_RULE, detail="会员余额不足或会员不存在")
    insert_balance_log(
        cur,
        member_id=member_id,
        balance_type=balance_type,
        amount=abs(change),
        order_id=order_id,
        external_ref=external_ref,
        created_at=created_at,
    )
    member = get_member(cur, member_id)
    if member is None:
        raise AppError(ErrCode.INTERNAL, detail="会员余额更新后读取失败")
    return member

