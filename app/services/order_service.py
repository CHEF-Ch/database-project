"""订单域业务层：下单、支付、取消、退款、状态流转与订单查询。

分层职责（docs/09 §2）：
  · 本层负责业务规则、角色可执行范围、事务边界与统一错误转换；
  · 所有 SQL 仍只在 ``app/dao`` 内；本层不拼 SQL、不碰游标细节；
  · ``member_id`` / ``employee_id`` 必须由页面层从 session 取后传入（本层不读
    Flask request）；客户端表单里的会员号/经办人/金额一律不采信；
  · 写操作统一走 ``db_cursor("app", commit=True)``：``role_app`` 是 v1.0 的写事务账户
    （见 ``sql/app_login.sql``），页面/权限矩阵仍先按 session 角色判一次。

一笔订单的写入范围（同一事务）：
  订单头 + 订单明细 + 加料明细 + 库存预占/确认/扣减 + SALE 流水
  + 券锁定/核销/退回 + 会员积分/余额快照与流水 + 平台订单信息。

已知限制（docs/09 §10）：
  · 配方/加料每份用量没有下单时快照；若下单后改配方，支付/取消时按**当前**配方
    重算耗用量，可能与创建时的预占不一致。v2.0 考虑在明细上快照耗用量。
  · 订单号用 ``MAX+1`` + ``UPDLOCK/HOLDLOCK``，撞主码时重试；不使用 SEQUENCE。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable

from app.dao import inventory as _inv
from app.dao import member as _member
from app.dao import order as _order
from app.dao import product as _product
from app.dao.connection import db_cursor
from app.errors import AppError, ErrCode
from app.services.permissions import current_role

# 与 docs/01 §4.6 / docs/02 §1.1 保持一致
SUGAR_LEVELS = {"FULL", "SEVENTY", "HALF", "THIRTY", "NONE"}
ICE_LEVELS = {"REGULAR", "LESS", "NONE"}
TEMP_LEVELS = {"HOT", "COLD"}
PAY_METHODS = {"CASH", "ONLINE", "BALANCE"}
ORDER_MODES = {"COUNTER_CASHIER", "COUNTER_SELF"}
PLATFORM_NAMES = {"MEITUAN", "ELEME"}
PLATFORM_DEFAULT_RATE = Decimal("0.2000")

DEFAULT_EMPLOYEE_ID = "E0000001"      # 自助单未绑定员工时，记收银员张伟（docs/02 §5.7 #1）
MAX_ITEMS = 50
MAX_ADDONS_PER_ITEM = 10
MAX_QTY = 99
ORDER_ID_RETRY = 3


# ---------------------------------------------------------------------------
# 基础校验
# ---------------------------------------------------------------------------
def _actor(role: str | None) -> str:
    role = role or current_role()
    if role is None:
        raise AppError(ErrCode.AUTH_REQUIRED)
    return role


def _read_role(role: str | None) -> str:
    """读操作也要求已登录，避免服务层被无 session 的调用方误用。"""
    role = role or current_role()
    if role is None:
        raise AppError(ErrCode.AUTH_REQUIRED)
    return role


def _money(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"金额不合法：{value!r}") from exc


def _rate(value) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError) as exc:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"费率不合法：{value!r}") from exc


def _text(value, *, label: str, max_len: int) -> str:
    text = (value or "").strip()
    if not text:
        raise AppError(ErrCode.PARAM_INVALID, f"{label}不能为空。")
    if len(text) > max_len:
        raise AppError(ErrCode.PARAM_INVALID, f"{label}过长。")
    return text


def _order_id(value) -> str:
    text = str(value or "").strip()
    if not text:
        raise AppError(ErrCode.PARAM_INVALID, "缺少订单号。")
    return text


def _enum(value, allowed: set[str], *, label: str) -> str:
    text = (value or "").strip().upper()
    if text not in allowed:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"{label}不合法：{value!r}")
    return text


def _qty(value, *, label: str = "数量", maximum: int = MAX_QTY) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AppError(ErrCode.PARAM_INVALID, f"{label}必须是整数。") from exc
    if number < 1 or number > maximum:
        raise AppError(ErrCode.PARAM_INVALID, f"{label}必须在 1—{maximum} 之间。")
    return number


def _as_date(value) -> date:
    return value.date() if isinstance(value, datetime) else value


# ---------------------------------------------------------------------------
# 输入规整
# ---------------------------------------------------------------------------
def _normalise_items(items: Iterable[dict]) -> list[dict]:
    if not isinstance(items, (list, tuple)) or not items:
        raise AppError(ErrCode.PARAM_INVALID, "订单至少需要一条明细。")
    if len(items) > MAX_ITEMS:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"订单明细不能超过 {MAX_ITEMS} 行")

    cleaned: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise AppError(ErrCode.PARAM_INVALID, detail="订单明细格式不合法")
        product_id = _text(raw.get("product_id"), label="商品编号", max_len=8)
        qty = _qty(raw.get("qty"), label="杯数")

        addons_raw = raw.get("addons") or []
        if not isinstance(addons_raw, (list, tuple)):
            raise AppError(ErrCode.PARAM_INVALID, detail="加料明细格式不合法")
        if len(addons_raw) > MAX_ADDONS_PER_ITEM:
            raise AppError(ErrCode.PARAM_INVALID, detail="单行加料不能超过 10 种")

        addons: list[dict] = []
        seen: set[str] = set()
        for addon in addons_raw:
            if not isinstance(addon, dict):
                raise AppError(ErrCode.PARAM_INVALID, detail="加料明细格式不合法")
            material_id = _text(addon.get("material_id"), label="加料编号", max_len=8)
            if material_id in seen:
                raise AppError(ErrCode.PARAM_INVALID, detail=f"加料重复：{material_id}")
            seen.add(material_id)
            addons.append(
                {"material_id": material_id, "qty": _qty(addon.get("qty"), label="加料份数")}
            )

        cleaned.append(
            {
                "product_id": product_id,
                "qty": qty,
                "sugar_level": _enum(raw.get("sugar_level") or "FULL", SUGAR_LEVELS, label="糖度"),
                "ice_level": _enum(raw.get("ice_level") or "REGULAR", ICE_LEVELS, label="冰量"),
                "temp_level": _enum(raw.get("temp_level") or "COLD", TEMP_LEVELS, label="温度"),
                "addons": addons,
            }
        )
    return cleaned


def _normalise_platform(platform: dict | None) -> dict:
    if not isinstance(platform, dict):
        raise AppError(ErrCode.PARAM_INVALID, "平台单缺少平台信息。")
    no = _text(platform.get("platform_order_no"), label="平台订单号", max_len=50)
    name = _enum(platform.get("platform_name"), PLATFORM_NAMES, label="平台名称")
    address = _text(platform.get("delivery_address"), label="配送地址", max_len=200)
    fee = _money(platform.get("delivery_fee") or 0)
    if fee < 0:
        raise AppError(ErrCode.PARAM_INVALID, "配送费不能为负。")
    raw_rate = platform.get("commission_rate")
    rate = _rate(PLATFORM_DEFAULT_RATE if raw_rate in (None, "") else raw_rate)
    if rate < 0 or rate > 1:
        raise AppError(ErrCode.PARAM_INVALID, "平台抽成比例必须在 0 到 1 之间。")
    return {
        "platform_order_no": no,
        "platform_name": name,
        "delivery_address": address,
        "delivery_fee": fee,
        "commission_rate": rate,
    }


def _normalise_context(
    *,
    role: str,
    channel: str | None,
    order_mode: str | None,
    pay_method: str | None,
    member_id: str | None,
    employee_id: str | None,
    platform: dict | None,
) -> dict:
    """把页面输入收敛成服务端可执行的下单上下文。

    顾客：只能到店自助（COUNTER_SELF），会员号必须来自 session，支付方式忽略，
          支付确认走 ``pay_order``；不能伪造平台单/经办人/退款额。
    店员：到店单（收银台/自助）；现金单直接 PAID，其余 PENDING。
    店长：到店单 + 平台单（平台单无会员、无 PENDING、无现金支付方式）。
    """
    if role not in {"clerk", "customer", "manager"}:
        raise AppError(ErrCode.FORBIDDEN)

    raw_channel = (channel or "COUNTER").strip().upper()
    if role == "customer":
        if raw_channel != "COUNTER" or platform:
            raise AppError(ErrCode.FORBIDDEN, detail="顾客不能创建平台单")
        if not member_id:
            raise AppError(ErrCode.BUSINESS_RULE, detail="顾客账号未绑定会员")
        return {
            "channel": "COUNTER",
            "order_mode": "COUNTER_SELF",
            "pay_method": None,
            "member_id": str(member_id).strip(),
            "employee_id": str(employee_id or "").strip() or DEFAULT_EMPLOYEE_ID,
            "platform": None,
        }

    employee = str(employee_id or "").strip()
    if not employee:
        raise AppError(ErrCode.INTERNAL, detail="当前账号未绑定员工编号（employee_id 缺失）")

    if raw_channel == "PLATFORM":
        if not platform:
            raise AppError(ErrCode.PARAM_INVALID, "平台单缺少平台信息。")
        return {
            "channel": "PLATFORM",
            "order_mode": None,
            "pay_method": None,
            "member_id": None,
            "employee_id": employee,
            "platform": _normalise_platform(platform),
        }

    if raw_channel != "COUNTER":
        raise AppError(ErrCode.PARAM_INVALID, detail=f"渠道不合法：{channel!r}")

    mode = _enum(order_mode or "COUNTER_CASHIER", ORDER_MODES, label="下单方式")
    method = None
    if pay_method:
        method = _enum(pay_method, PAY_METHODS, label="支付方式")
    return {
        "channel": "COUNTER",
        "order_mode": mode,
        "pay_method": method,
        "member_id": str(member_id).strip() if member_id else None,
        "employee_id": employee,
        "platform": None,
    }


# ---------------------------------------------------------------------------
# 事务内的小工具
# ---------------------------------------------------------------------------
def _load_lines(cur, lines: list[dict]) -> tuple[Decimal, dict[str, Decimal]]:
    """校验商品/加料、计算金额与原料耗用（全部取当前主数据价/用量）。"""
    subtotal = Decimal("0.00")
    usages: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for line in lines:
        product = _product.get_product(cur, line["product_id"], for_update=True)
        if product is None:
            raise AppError(ErrCode.NOT_FOUND, detail=f"商品不存在：{line['product_id']}")
        if product["sale_status"] != "ON_SALE":
            raise AppError(ErrCode.BUSINESS_RULE, detail=f"商品已下架：{line['product_id']}")

        size = product["size"]
        unit_price = _money(product["unit_price"])
        line["size"] = size
        line["unit_price"] = unit_price
        subtotal += unit_price * line["qty"]

        recipe = _product.list_recipe(cur, product["product_id"])
        if not recipe:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"商品没有配方，无法扣料：{product['product_id']}",
            )
        for row in recipe:
            usages[row["material_id"]] += _money(row["qty"]) * line["qty"]

        for addon in line["addons"]:
            option = _product.get_addon_option(
                cur, product["product_id"], addon["material_id"], for_update=True
            )
            if option is None:
                raise AppError(
                    ErrCode.BUSINESS_RULE,
                    detail=f"商品 {product['product_id']} 不支持加料 {addon['material_id']}",
                )
            if option["material_kind"] != "ADDON":
                raise AppError(
                    ErrCode.BUSINESS_RULE,
                    detail=f"加料 {addon['material_id']} 不是 ADDON",
                )
            if option["addon_status"] != "AVAILABLE":
                raise AppError(
                    ErrCode.BUSINESS_RULE,
                    detail=f"加料 {addon['material_id']} 当前不可选",
                )
            addon_price = _money(option["addon_price"])
            addon_qty = _money(option["addon_qty"])
            addon["addon_price"] = addon_price
            addon["addon_qty"] = addon_qty
            subtotal += addon_price * addon["qty"]
            usages[addon["material_id"]] += addon_qty * addon["qty"]

    return _money(subtotal), dict(usages)


def _apply_coupon(
    cur,
    *,
    member_id: str | None,
    member_coupon_id: int | None,
    subtotal: Decimal,
    today: date | None = None,
) -> tuple[Decimal, dict | None]:
    """校验券并返回（优惠额，券行）；不在这里锁券（订单头还没插入）。"""
    if not member_coupon_id:
        return Decimal("0.00"), None
    if not member_id:
        raise AppError(ErrCode.PARAM_INVALID, "使用券必须关联会员。")

    try:
        coupon_id = int(member_coupon_id)
    except (TypeError, ValueError) as exc:
        raise AppError(ErrCode.PARAM_INVALID, "券编号不合法。") from exc
    coupon = _member.get_member_coupon(cur, coupon_id, for_update=True)
    if coupon is None or coupon["member_id"] != member_id:
        raise AppError(ErrCode.BUSINESS_RULE, "券不存在或不属于当前会员。")
    if coupon["coupon_status"] != "UNUSED" or coupon["used_order_id"] is not None:
        raise AppError(ErrCode.BUSINESS_RULE, "券已使用或已锁定。")

    today = today or date.today()
    valid_from = _as_date(coupon["valid_from"])
    valid_to = _as_date(coupon["valid_to"])
    if not (valid_from <= today <= valid_to):
        raise AppError(ErrCode.BUSINESS_RULE, "券不在有效期内。")

    threshold = _money(coupon["threshold_amount"])
    if subtotal < threshold:
        raise AppError(
            ErrCode.BUSINESS_RULE,
            detail=f"未达到券门槛：{subtotal} < {threshold}",
        )
    return _money(coupon["discount_amount"]), coupon


def _usages_for_items(cur, items: list[dict]) -> dict[str, Decimal]:
    """按订单明细重算原料耗用（支付/取消时用；不改价，只读配方/加料用量）。"""
    usages: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for item in items:
        recipe = _product.list_recipe(cur, item["product_id"])
        if not recipe:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"商品没有配方，无法还原耗用：{item['product_id']}",
            )
        for row in recipe:
            usages[row["material_id"]] += _money(row["qty"]) * int(item["qty"])
        for addon in item.get("addons", []):
            option = _product.get_addon_option(cur, item["product_id"], addon["material_id"])
            if option is None:
                raise AppError(
                    ErrCode.BUSINESS_RULE,
                    detail=f"加料选项已不存在：{addon['material_id']}",
                )
            usages[addon["material_id"]] += _money(option["addon_qty"]) * int(addon["qty"])
    return dict(usages)


def _is_order_id_collision(exc: AppError) -> bool:
    """只对订单主码冲突重试，不吞其它约束错误。"""
    return exc.code == ErrCode.DB_CONSTRAINT and "PK_shop_order" in (exc.detail or "")


def _points(total: Decimal) -> int:
    """满 1 元记 1 分；向零取整，不产生小数分（docs/01 §4.1）。"""
    return int(total)


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------
def create_order(
    *,
    items: Iterable[dict],
    role: str | None = None,
    member_id: str | None = None,
    employee_id: str | None = None,
    channel: str | None = "COUNTER",
    order_mode: str | None = None,
    pay_method: str | None = None,
    member_coupon_id: int | None = None,
    platform: dict | None = None,
    now: datetime | None = None,
    today: date | None = None,
) -> dict:
    """创建一笔订单，返回 ``{order, items, platform}``。

    - 现金到店单 / 平台单：直接 ``PAID``，库存当场确认/扣减并写 SALE 流水；
    - 自助/线上/余额到店单：``PENDING``，只预占 ``reserved_qty``，支付由 ``pay_order`` 完成；
    - 券：现金单当场核销，PENDING 单先锁定；取消/退款时退回；
    - 金额：服务端按下单时商品价与加料价快照计算，客户端传的金额一律忽略。
    """
    role = _actor(role)
    context = _normalise_context(
        role=role,
        channel=channel,
        order_mode=order_mode,
        pay_method=pay_method,
        member_id=member_id,
        employee_id=employee_id,
        platform=platform,
    )
    lines = _normalise_items(items)
    created_at = now or datetime.now()

    for attempt in range(ORDER_ID_RETRY):
        try:
            with db_cursor("app", commit=True) as cur:
                return _create_order_tx(
                    cur,
                    lines=lines,
                    context=context,
                    member_coupon_id=member_coupon_id,
                    created_at=created_at,
                    today=today,
                )
        except AppError as exc:
            if attempt < ORDER_ID_RETRY - 1 and _is_order_id_collision(exc):
                continue
            raise


def _create_order_tx(
    cur,
    *,
    lines: list[dict],
    context: dict,
    member_coupon_id: int | None,
    created_at: datetime,
    today: date | None = None,
) -> dict:
    subtotal, usages = _load_lines(cur, lines)
    discount, coupon = _apply_coupon(
        cur,
        member_id=context["member_id"],
        member_coupon_id=member_coupon_id,
        subtotal=subtotal,
        today=today,
    )
    total = _money(subtotal - discount)
    if total < 0:
        raise AppError(ErrCode.BUSINESS_RULE, detail="券优惠超过券前金额")

    channel = context["channel"]
    method = context["pay_method"]
    status = "PAID" if channel == "PLATFORM" or method == "CASH" else "PENDING"
    paid_at = created_at if status == "PAID" else None

    # 预占：现金单先预占再确认（净效果是扣现存）；PENDING 只预占；平台单不预占。
    if status == "PENDING" or (status == "PAID" and channel == "COUNTER"):
        _inv.reserve_order_stock(cur, usages)

    order_id = _order.next_order_id(cur)
    order = _order.insert_order(
        cur,
        order_id=order_id,
        channel=channel,
        order_mode=context["order_mode"],
        order_status=status,
        member_id=context["member_id"],
        employee_id=context["employee_id"],
        item_subtotal=subtotal,
        coupon_discount=discount,
        total_amount=total,
        pay_method=(method if status == "PAID" and channel == "COUNTER" else None),
        created_at=created_at,
        paid_at=paid_at,
    )

    item_line = 0
    for line in lines:
        item_line += 1
        _order.insert_order_item(
            cur,
            order_id=order_id,
            item_line=item_line,
            product_id=line["product_id"],
            size=line["size"],
            qty=line["qty"],
            unit_price=line["unit_price"],
            sugar_level=line["sugar_level"],
            ice_level=line["ice_level"],
            temp_level=line["temp_level"],
        )
        for addon in line["addons"]:
            _order.insert_order_addon(
                cur,
                order_id=order_id,
                item_line=item_line,
                material_id=addon["material_id"],
                qty=addon["qty"],
                addon_price=addon["addon_price"],
            )

    if channel == "PLATFORM":
        platform = context["platform"]
        commission = _money(total * platform["commission_rate"])
        _order.insert_order_platform(
            cur,
            order_id=order_id,
            platform_order_no=platform["platform_order_no"],
            platform_name=platform["platform_name"],
            delivery_address=platform["delivery_address"],
            delivery_fee=platform["delivery_fee"],
            commission_rate=platform["commission_rate"],
            commission_amount=commission,
        )

    if coupon is not None:
        if status == "PAID":
            if _member.use_coupon(
                cur, coupon["member_coupon_id"], context["member_id"], order_id
            ) != 1:
                raise AppError(ErrCode.BUSINESS_RULE, "券核销失败，请重试。")
        else:
            if _member.lock_coupon(
                cur, coupon["member_coupon_id"], context["member_id"], order_id
            ) != 1:
                raise AppError(ErrCode.BUSINESS_RULE, "券锁定失败，请重试。")

    if status == "PAID" and channel == "COUNTER":
        _inv.confirm_order_stock(
            cur,
            order_id=order_id,
            usages=usages,
            employee_id=context["employee_id"],
            created_at=created_at,
        )
    elif channel == "PLATFORM":
        _inv.deduct_order_stock(
            cur,
            order_id=order_id,
            usages=usages,
            employee_id=context["employee_id"],
            created_at=created_at,
        )

    # 现金单当场支付：会员到店非余额支付计分；平台单不计分。
    if status == "PAID" and channel == "COUNTER" and context["member_id"]:
        earned = _points(total)
        if earned > 0:
            _member.add_member_points(
                cur,
                member_id=context["member_id"],
                delta=earned,
                point_type="EARN_SALE",
                order_id=order_id,
                created_at=created_at,
            )

    return _order.get_order_detail(cur, order_id)


# ---------------------------------------------------------------------------
# 支付 / 取消 / 退款 / 状态
# ---------------------------------------------------------------------------
def pay_order(
    order_id: str,
    *,
    pay_method: str,
    pay_txn_no: str | None = None,
    role: str | None = None,
    member_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """支付确认：``PENDING → PAID``，同事务确认库存、核销券、扣余额/计积分。

    - 幂等：同一 ``pay_txn_no`` 已对应本单且已 PAID，直接返回；对应其它单 → 409；
    - 余额支付：先锁会员并校验余额，扣减余额 + 写 SPEND 流水，**不计积分**；
    - 线上/现金：会员到店单按实付计分；平台单不走本函数。
    """
    role = _actor(role)
    if role not in {"clerk", "customer", "manager"}:
        raise AppError(ErrCode.FORBIDDEN)
    order_id = _order_id(order_id)
    method = _enum(pay_method, PAY_METHODS, label="支付方式")
    txn = (pay_txn_no or "").strip() or None
    if method == "ONLINE" and not txn:
        raise AppError(ErrCode.PARAM_INVALID, "线上支付必须提供支付流水号。")
    paid_at = now or datetime.now()

    with db_cursor("app", commit=True) as cur:
        order = _order.get_order(cur, order_id, for_update=True)
        if order is None:
            raise AppError(ErrCode.NOT_FOUND)
        if role == "customer" and (
            not member_id or order["member_id"] != member_id
        ):
            raise AppError(ErrCode.NOT_FOUND)

        if order["order_status"] == "PAID":
            if txn and order["pay_txn_no"] == txn:
                return _order.get_order_detail(cur, order_id)
            raise AppError(ErrCode.BUSINESS_RULE, "订单已支付，不能重复支付。")
        if order["order_status"] != "PENDING":
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"订单状态 {order['order_status']} 不能支付",
            )

        if txn:
            other = _order.find_order_by_pay_txn(cur, txn)
            if other is not None and other["order_id"] != order_id:
                raise AppError(ErrCode.BUSINESS_RULE, "支付流水号已用于其它订单。")

        total = _money(order["total_amount"])
        if method == "BALANCE":
            if not order["member_id"]:
                raise AppError(ErrCode.BUSINESS_RULE, "该订单不是会员单，不能用余额支付。")
            member = _member.get_member(cur, order["member_id"], for_update=True)
            if member is None:
                raise AppError(ErrCode.BUSINESS_RULE, "会员不存在。")
            if _money(member["balance"]) < total:
                raise AppError(ErrCode.BUSINESS_RULE, "会员余额不足。")
            _member.add_member_balance(
                cur,
                member_id=order["member_id"],
                delta=-total,
                balance_type="SPEND",
                order_id=order_id,
                created_at=paid_at,
            )

        detail = _order.get_order_detail(cur, order_id)
        usages = _usages_for_items(cur, detail["items"])
        _inv.confirm_order_stock(
            cur,
            order_id=order_id,
            usages=usages,
            employee_id=order["employee_id"],
            created_at=paid_at,
        )

        coupon = _member.get_coupon_for_order(cur, order_id)
        if coupon is not None and coupon["coupon_status"] == "LOCKED":
            if _member.use_coupon(
                cur, coupon["member_coupon_id"], order["member_id"], order_id
            ) != 1:
                raise AppError(ErrCode.BUSINESS_RULE, "券核销失败，请重试。")

        if order["member_id"] and order["channel"] == "COUNTER" and method != "BALANCE":
            earned = _points(total)
            if earned > 0:
                _member.add_member_points(
                    cur,
                    member_id=order["member_id"],
                    delta=earned,
                    point_type="EARN_SALE",
                    order_id=order_id,
                    created_at=paid_at,
                )

        if _order.update_order_payment(
            cur,
            order_id=order_id,
            from_status="PENDING",
            pay_method=method,
            pay_txn_no=txn if method == "ONLINE" else None,
            paid_at=paid_at,
        ) != 1:
            raise AppError(ErrCode.BUSINESS_RULE, "订单状态已变化，支付未完成。")

        return _order.get_order_detail(cur, order_id)


def cancel_order(
    order_id: str,
    *,
    role: str | None = None,
    member_id: str | None = None,
) -> dict:
    """未付款取消：仅 ``PENDING → CANCELLED``，释放预占并退回券。"""
    role = _actor(role)
    if role not in {"clerk", "customer", "manager"}:
        raise AppError(ErrCode.FORBIDDEN)
    order_id = _order_id(order_id)

    with db_cursor("app", commit=True) as cur:
        order = _order.get_order(cur, order_id, for_update=True)
        if order is None:
            raise AppError(ErrCode.NOT_FOUND)
        if role == "customer" and (
            not member_id or order["member_id"] != member_id
        ):
            raise AppError(ErrCode.NOT_FOUND)
        if order["order_status"] != "PENDING":
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"订单状态 {order['order_status']} 不能取消（已付款请走退款）",
            )

        detail = _order.get_order_detail(cur, order_id)
        usages = _usages_for_items(cur, detail["items"])
        if not _order.cancel_order_with_stock(cur, order_id, usages):
            raise AppError(ErrCode.BUSINESS_RULE, "订单状态已变化，取消失败。")

        coupon = _member.get_coupon_for_order(cur, order_id)
        if coupon is not None and coupon["coupon_status"] in {"LOCKED", "USED"}:
            if _member.return_coupon(
                cur, coupon["member_coupon_id"], order["member_id"], order_id
            ) != 1:
                raise AppError(ErrCode.BUSINESS_RULE, "券退回失败，请重试。")

        return _order.get_order_detail(cur, order_id)


def refund_order(
    order_id: str,
    *,
    role: str | None = None,
) -> dict:
    """整单退款：``PAID/MAKING/COMPLETED → REFUNDED``。

    - 退款额取订单 ``total_amount``（忽略客户端金额，防篡改）；
    - 余额支付退回余额并写 REFUND 流水；积分按本单已记分冲正（REVERSAL）；
    - 券退回；**库存不恢复**（docs/01 §4.6：已耗用原料不追回）。
    """
    role = _actor(role)
    if role != "manager":
        raise AppError(ErrCode.FORBIDDEN)
    order_id = _order_id(order_id)

    with db_cursor("app", commit=True) as cur:
        order = _order.get_order(cur, order_id, for_update=True)
        if order is None:
            raise AppError(ErrCode.NOT_FOUND)
        if order["order_status"] not in {"PAID", "MAKING", "COMPLETED"}:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"订单状态 {order['order_status']} 不能退款",
            )

        total = _money(order["total_amount"])
        if _order.refund_order(
            cur, order_id=order_id, refund_amount=total
        ) != 1:
            raise AppError(ErrCode.BUSINESS_RULE, "订单状态已变化，退款失败。")

        if order["pay_method"] == "BALANCE":
            if not order["member_id"]:
                raise AppError(ErrCode.BUSINESS_RULE, "余额单缺少会员，无法退回余额。")
            _member.add_member_balance(
                cur,
                member_id=order["member_id"],
                delta=total,
                balance_type="REFUND",
                order_id=order_id,
            )

        earned = _member.sum_points_for_order(cur, order_id)
        if earned > 0 and order["member_id"]:
            _member.add_member_points(
                cur,
                member_id=order["member_id"],
                delta=-earned,
                point_type="REVERSAL",
                order_id=order_id,
            )

        coupon = _member.get_coupon_for_order(cur, order_id)
        if coupon is not None and coupon["coupon_status"] in {"LOCKED", "USED"}:
            if _member.return_coupon(
                cur, coupon["member_coupon_id"], order["member_id"], order_id
            ) != 1:
                raise AppError(ErrCode.BUSINESS_RULE, "券退回失败，请重试。")

        return _order.get_order_detail(cur, order_id)


def update_order_status(
    order_id: str,
    *,
    to_status: str,
    role: str | None = None,
) -> dict:
    """制作状态流转：仅 ``PAID → MAKING → COMPLETED``（制作员）。"""
    role = _actor(role)
    if role != "maker":
        raise AppError(ErrCode.FORBIDDEN)
    order_id = _order_id(order_id)
    target = _enum(to_status, {"MAKING", "COMPLETED"}, label="订单状态")
    allowed = {"PAID": "MAKING", "MAKING": "COMPLETED"}

    with db_cursor("app", commit=True) as cur:
        order = _order.get_order(cur, order_id, for_update=True)
        if order is None:
            raise AppError(ErrCode.NOT_FOUND)
        current = order["order_status"]
        if allowed.get(current) != target:
            raise AppError(
                ErrCode.BUSINESS_RULE,
                detail=f"非法状态流转：{current} → {target}",
            )
        if _order.update_order_status(
            cur, order_id=order_id, from_status=current, to_status=target
        ) != 1:
            raise AppError(ErrCode.BUSINESS_RULE, "订单状态已变化，更新失败。")
        return _order.get_order_detail(cur, order_id)


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def list_orders(
    *,
    status: str | None = None,
    channel: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = 100,
    offset: int | None = 0,
    member_id: str | None = None,
    role: str | None = None,
) -> list[dict]:
    """订单列表；顾客强制只看自己的单（member_id 来自 session）。"""
    role = _read_role(role)
    with db_cursor(role) as cur:
        if role == "customer":
            if not member_id:
                return []
            return _order.list_orders_by_member(
                cur, member_id, limit=limit, offset=offset
            )
        return _order.list_orders(
            cur,
            status=status,
            channel=channel,
            q=q,
            date_from=date_from,
            date_to=date_to,
            member_id=member_id,
            limit=limit,
            offset=offset,
        )


def list_my_orders(
    *,
    member_id: str | None,
    limit: int | None = 100,
    offset: int | None = 0,
    role: str | None = None,
) -> list[dict]:
    """“我的订单”：只按 session 会员过滤；未绑定会员（如店员）返回空列表。"""
    role = _read_role(role)
    if not member_id:
        return []
    with db_cursor(role) as cur:
        return _order.list_orders_by_member(
            cur, member_id, limit=limit, offset=offset
        )


def get_order_detail(
    order_id: str,
    *,
    member_id: str | None = None,
    include_stock_logs: bool = False,
    role: str | None = None,
) -> dict:
    """订单详情；顾客按会员做行级隔离，不存在/不属于自己统一 404。"""
    role = _read_role(role)
    order_id = _order_id(order_id)
    with db_cursor(role) as cur:
        if role == "customer":
            detail = _order.get_order_detail_for_member(cur, order_id, member_id or "")
        else:
            detail = _order.get_order_detail(cur, order_id)
        if detail is None:
            raise AppError(ErrCode.NOT_FOUND)
        if include_stock_logs and role == "manager":
            detail["stock_logs"] = _inv.list_stock_logs(cur, order_id=order_id)
        return detail


def list_usable_coupons(
    member_id: str,
    subtotal,
    *,
    today: date | None = None,
    role: str | None = None,
) -> list[dict]:
    """当前会员可用于该金额的券（未使用、有效期内、门槛满足）。"""
    role = _read_role(role)
    if not member_id:
        return []
    with db_cursor(role) as cur:
        return _member.list_usable_coupons(
            cur, member_id, subtotal=_money(subtotal), today=today
        )
