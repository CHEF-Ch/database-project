"""订单页面/接口（页面层）。

⑤ 落地：订单列表 / 我的订单 / 订单详情 / 下单 / 支付 / 取消 / 退款 / 制作状态。
页面层只做四件事：接参、表单基础校验、调业务层、渲染与重定向；金额、库存是否
充足、券能不能用、状态能不能跳，一律由 ``order_service`` 判定（④），SQL 只在
``app/dao`` 内。权限由 ``role_required`` / ``role_required_any`` 在路由层挡一道，
数据库层再按角色受限登录兜底。

三条口径（与 docs/09 §10、docs/09-应用开发/测试用例.md §4 一致）：
  1. ``member_id`` 对顾客**只从 session 取**，表单里传什么都不采信（防 ORD-09 越权单）；
  2. **只有店员能下单**（店长无 ``order.create``，见权限矩阵），店员可在下单页录入会员号做代客下单；
     券下拉按该会员号查（需以 ``/orders/new?member_id=C0000002`` 形式进入或页内“查询该会员券”）；
  3. 行级隔离（顾客只看自己的单）由 ``order_service`` 按 ``member_id`` 过滤，
     页面层不做“看着像就放行”的判断。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask import Blueprint, current_app, g, redirect, render_template, request, session, url_for

from app.errors import AppError, ErrCode
from app.services import order_service, product_service
from app.services.permissions import (
    PERMISSIONS,
    current_role,
    role_required,
    role_required_any,
)

bp = Blueprint("order", __name__, url_prefix="/orders")

# ---- 展示用词表（数据库枚举 → 中文）----------------------------------------
# 只影响页面文案，不参与任何业务判断；业务判断在 order_service 里。
STATUS_LABELS = {
    "PENDING": "待支付",
    "PAID": "已支付",
    "MAKING": "制作中",
    "COMPLETED": "已完成",
    "CANCELLED": "已取消",
    "REFUNDED": "已退款",
}
CHANNEL_LABELS = {"COUNTER": "到店", "PLATFORM": "外卖平台"}
MODE_LABELS = {"COUNTER_CASHIER": "收银台", "COUNTER_SELF": "自助点单"}
PAY_LABELS = {"CASH": "现金", "ONLINE": "线上", "BALANCE": "会员余额"}
PLATFORM_LABELS = {"MEITUAN": "美团", "ELEME": "饿了么"}
SUGAR_LABELS = {
    "FULL": "全糖",
    "SEVENTY": "七分糖",
    "HALF": "半糖",
    "THIRTY": "三分糖",
    "NONE": "无糖",
}
ICE_LABELS = {"REGULAR": "正常冰", "LESS": "少冰", "NONE": "去冰"}
TEMP_LABELS = {"HOT": "热", "COLD": "冰"}

ORDER_STATUSES = tuple(STATUS_LABELS)
PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# 券下拉的“金额上界”哨兵：GET 阶段还不知道顾客会点几杯，无法算出真实券前金额。
# 这里按上界列出“有效期内且未使用”的券（**可能包含本单还不满门槛的券**），页面上标注门槛；
# 能不能用由 order_service._apply_coupon 按实际券前金额校验（不满足则 409 E4001）。
# 口径：展示宽、判定严；代价是选错券会整单被拒且表单内容丢失（v1.0 接受）。
_COUPON_PREVIEW_CEILING = Decimal("999999.00")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _perms() -> set[str]:
    """当前角色的权限点集合，供模板做按钮显隐（体验控制）。"""
    return PERMISSIONS.get(current_role() or "", set())


def _dec(value) -> Decimal:
    """金额展示用：两位小数。"""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _text(name: str) -> str:
    return (request.args.get(name) or "").strip()


def _int_arg(name: str, default: int, *, low: int, high: int) -> int:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise AppError(ErrCode.PARAM_INVALID, f"{name} 必须是整数。") from None
    return max(low, min(high, value))


def _date_arg(name: str) -> date | None:
    """``YYYY-MM-DD`` → date；空串返回 None；非法值按 400 处理。"""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise AppError(ErrCode.PARAM_INVALID, f"{name} 日期格式应为 YYYY-MM-DD。") from None


def _paging() -> tuple[int, int]:
    limit = _int_arg("limit", PAGE_SIZE, low=1, high=MAX_PAGE_SIZE)
    offset = _int_arg("offset", 0, low=0, high=10_000_000)
    return limit, offset


def _page_url(endpoint: str, **overrides) -> str:
    """翻页链接：在当前查询串基础上只改 offset，其它筛选条件不丢。"""
    args = request.args.to_dict()
    args.update({key: str(value) for key, value in overrides.items()})
    return url_for(endpoint, **args)


def _decorate_items(items: list[dict]) -> list[dict]:
    """给明细补展示字段（小计、糖/冰/温中文）；金额取自订单快照，不是当前价。"""
    for item in items:
        item["amount"] = _dec(item["unit_price"]) * int(item["qty"])
        item["sugar_label"] = SUGAR_LABELS.get(item["sugar_level"], item["sugar_level"])
        item["ice_label"] = ICE_LABELS.get(item["ice_level"], item["ice_level"])
        item["temp_label"] = TEMP_LABELS.get(item["temp_level"], item["temp_level"])
        for addon in item.get("addons") or []:
            addon["amount"] = _dec(addon["addon_price"]) * int(addon["qty"])
    return items


def _orderable_products() -> list[dict]:
    """可下单商品（在售）+ 每个商品当前可选的加料，供下单表单渲染。"""
    products = product_service.list_products(sale_status="ON_SALE")
    for product in products:
        detail = product_service.get_product_detail(product["product_id"]) or {}
        product["addons"] = [
            addon
            for addon in detail.get("addons", [])
            if addon.get("addon_status") == "AVAILABLE"
        ]
    return products


def _customer_only_member(role: str | None) -> str | None:
    """下单页的会员号：顾客只能来自 session；店员可用 URL 上的 ``member_id``（用于列该会员的券）。"""
    if role == "customer":
        return session.get("member_id")
    return (request.args.get("member_id") or "").strip() or None


def _member_for_submit(role: str | None) -> str | None:
    """下单时的会员号：顾客只认 session，绝不采信表单（防伪造他人会员单）。"""
    if role == "customer":
        return session.get("member_id")
    return (request.form.get("member_id") or "").strip() or None


def _collect_items(products: list[dict]) -> list[dict]:
    """把表单里的逐商品勾选还原成 ``order_service.create_order`` 的 items。

    只遍历**服务端刚查出来的在售商品**（不听表单里的 product_id），因此提交里多塞
    的商品会被忽略；数量/加料原样交给业务层校验（range、是否属于该商品、是否 ADDON）。
    """
    items: list[dict] = []
    for product in products:
        product_id = product["product_id"]
        qty = (request.form.get(f"qty.{product_id}") or "").strip()
        if qty in ("", "0"):
            continue
        addons = []
        for option in product.get("addons") or []:
            material_id = option["material_id"]
            raw = (request.form.get(f"addon.{product_id}.{material_id}") or "").strip()
            if raw in ("", "0"):
                continue
            addons.append({"material_id": material_id, "qty": raw})
        items.append(
            {
                "product_id": product_id,
                "qty": qty,
                "sugar_level": request.form.get(f"sugar.{product_id}") or "FULL",
                "ice_level": request.form.get(f"ice.{product_id}") or "REGULAR",
                "temp_level": request.form.get(f"temp.{product_id}") or "COLD",
                "addons": addons,
            }
        )
    return items


def _platform_from_form() -> dict:
    """平台单信息；空字段原样交给业务层判必填（不在页面层重复一份规则）。"""
    return {
        "platform_order_no": request.form.get("platform_order_no"),
        "platform_name": request.form.get("platform_name"),
        "delivery_address": request.form.get("delivery_address"),
        "delivery_fee": request.form.get("delivery_fee"),
        "commission_rate": request.form.get("commission_rate"),
    }


def _coupon_id_from_form() -> int | None:
    raw = (request.form.get("member_coupon_id") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise AppError(ErrCode.PARAM_INVALID, "券编号必须是整数。") from None


def _detail_actions(order: dict, perms: set[str]) -> set[str]:
    """详情页允许显示的操作按钮（只是体验控制，真正的校验在业务层）。

    ⚠ 这里的状态集合是**展示用的副本**，真实状态机在 ``order_service``
    （``pay_order``/``cancel_order``/``refund_order``/``update_order_status``）。
    两边若不一致，以业务层为准（改状态机时记得同步本函数）。
    """
    status = order["order_status"]
    actions: set[str] = set()
    if "order.create" in perms and status == "PENDING":
        actions.update({"pay", "cancel"})
    if "order.refund" in perms and status in {"PAID", "MAKING", "COMPLETED"}:
        actions.add("refund")
    if "order.update_status" in perms:
        if status == "PAID":
            actions.add("to_making")
        elif status == "MAKING":
            actions.add("to_completed")
    return actions


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
@bp.get("/")
@role_required("order.read")
def list_orders():
    status = _text("status").upper()
    if status and status not in ORDER_STATUSES:
        raise AppError(ErrCode.PARAM_INVALID, f"订单状态不合法：{status}")
    channel = _text("channel").upper()
    if channel and channel not in CHANNEL_LABELS:
        raise AppError(ErrCode.PARAM_INVALID, f"渠道不合法：{channel}")

    limit, offset = _paging()
    # 列表页的会员号筛选是**员工功能**：本端点需 `order.read`（顾客进不来），
    # 所以这里可以接受 URL 上的 member_id；顾客一律走 /orders/mine（member_id 取自 session）。
    # 这是 order_service.list_orders docstring “不得透传”的唯一豁免点，改动前请先看该 docstring。
    member_filter = None if current_role() == "customer" else (_text("member_id") or None)
    filters = {
        "status": status or None,
        "channel": channel or None,
        "q": _text("q") or None,
        "member_id": member_filter,
        "date_from": _date_arg("date_from"),
        "date_to": _date_arg("date_to"),
    }
    # 多取一行用来判断“还有下一页”，页面上只渲染 limit 行
    rows = order_service.list_orders(limit=limit + 1, offset=offset, **filters)
    has_next = len(rows) > limit
    return render_template(
        "orders/list.html",
        orders=rows[:limit],
        has_next=has_next,
        prev_url=_page_url("order.list_orders", offset=max(0, offset - limit)) if offset else None,
        next_url=_page_url("order.list_orders", offset=offset + limit) if has_next else None,
        limit=limit,
        offset=offset,
        filters={
            "status": status,
            "channel": channel,
            "q": filters["q"] or "",
            "member_id": filters["member_id"] or "",
            "date_from": request.args.get("date_from", ""),
            "date_to": request.args.get("date_to", ""),
        },
        statuses=STATUS_LABELS,
        channels=CHANNEL_LABELS,
        modes=MODE_LABELS,
        pays=PAY_LABELS,
        perms=_perms(),
    )


@bp.get("/mine")
@role_required("order.read_own")
def my_orders():
    """顾客/店员的“我的订单”。

    必须按 session 里的 ``member_id`` 过滤：``role_customer`` 对 ``shop_order`` 是
    表级 SELECT，数据库不会替我们拦行。店员有 ``order.read_own`` 但账号不绑会员，
    按约定返回空列表 + 提示（测试用例 §8 第 1 条）。
    """
    member_id = session.get("member_id")
    limit, offset = _paging()
    rows = order_service.list_my_orders(
        member_id=member_id, limit=limit + 1, offset=offset
    )
    has_next = len(rows) > limit
    return render_template(
        "orders/mine.html",
        orders=rows[:limit],
        member_id=member_id,
        has_next=has_next,
        prev_url=_page_url("order.my_orders", offset=max(0, offset - limit)) if offset else None,
        next_url=_page_url("order.my_orders", offset=offset + limit) if has_next else None,
        limit=limit,
        offset=offset,
        statuses=STATUS_LABELS,
        channels=CHANNEL_LABELS,
        modes=MODE_LABELS,
        pays=PAY_LABELS,
        perms=_perms(),
    )


@bp.get("/<order_id>")
@role_required_any("order.read", "order.read_own")
def detail(order_id):
    """订单详情；顾客按会员做行级隔离（不属于自己 → 404，不泄露是否存在）。"""
    role = current_role()
    detail_data = order_service.get_order_detail(
        order_id,
        member_id=session.get("member_id"),
        include_stock_logs=(role == "manager"),
    )
    order = detail_data["order"]
    perms = _perms()
    return render_template(
        "orders/detail.html",
        order=order,
        items=_decorate_items(detail_data["items"]),
        platform=detail_data.get("platform"),
        stock_logs=detail_data.get("stock_logs") or [],
        actions=_detail_actions(order, perms),
        perms=perms,
        statuses=STATUS_LABELS,
        channels=CHANNEL_LABELS,
        modes=MODE_LABELS,
        pays=PAY_LABELS,
        platforms=PLATFORM_LABELS,
    )


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------
def _form_context(
    *,
    role: str | None,
    products: list[dict],
    member_id: str | None,
    form=None,
    error: AppError | None = None,
) -> dict:
    """下单表单的渲染上下文（GET 首次渲染与 POST 校验失败回填共用）。

    ``form`` 非空时表示“提交失败后回填”：模板从它里取值（杯数/糖冰温/加料/券/平台字段），
    并把 ``error`` 渲染成页面顶部的提示条。
    """
    coupons = (
        order_service.list_usable_coupons(member_id, _COUPON_PREVIEW_CEILING)
        if member_id
        else []
    )
    return {
        "products": products,
        "coupons": coupons,
        "member_id": member_id or "",
        "is_customer": (role == "customer"),
        "pays": PAY_LABELS,
        "modes": MODE_LABELS,
        "platforms": PLATFORM_LABELS,
        "sugars": SUGAR_LABELS,
        "ices": ICE_LABELS,
        "temps": TEMP_LABELS,
        "perms": _perms(),
        "form": form,
        "error": error,
    }


# 可以“回填重渲染”的错误：都是用户/输入层面可修正的；其余（403 无权限、500 内部错误、
# 503 库不可用、500 事务失败）仍交给全局异常处理器渲染错误页，避免把系统故障当成表单错误。
_FORM_LOCAL_ERRORS = frozenset(
    {ErrCode.PARAM_INVALID, ErrCode.BUSINESS_RULE, ErrCode.NOT_FOUND, ErrCode.DB_CONSTRAINT}
)


@bp.get("/new")
@role_required("order.create")
def new_order():
    role = current_role()
    return render_template(
        "orders/form.html",
        **_form_context(
            role=role,
            products=_orderable_products(),
            member_id=_customer_only_member(role),
        ),
    )


@bp.post("/")
@role_required("order.create")
def create_order():
    role = current_role()
    products = _orderable_products()
    try:
        items = _collect_items(products)
        if not items:
            raise AppError(ErrCode.PARAM_INVALID, "请至少选择一杯饮品（杯数填 1—99）。")

        channel = (request.form.get("channel") or "COUNTER").strip().upper()
        created = order_service.create_order(
            items=items,
            member_id=_member_for_submit(role),
            employee_id=session.get("employee_id"),
            channel=channel,
            order_mode=(request.form.get("order_mode") or "").strip() or None,
            pay_method=(request.form.get("pay_method") or "").strip() or None,
            member_coupon_id=_coupon_id_from_form(),
            platform=_platform_from_form() if channel == "PLATFORM" else None,
        )
    except AppError as err:
        # 表单错误在页面层就地回填重渲染（WTForms/Rails 的做法）：用户不用重填；
        # 错误明细仍写日志（全局处理器不再经手，故这里自己记一份带 rid 的）
        if err.code not in _FORM_LOCAL_ERRORS:
            raise
        current_app.logger.warning(
            "rid=%s form-error code=%s path=%s detail=%s",
            getattr(g, "request_id", "-"),
            err.code,
            request.path,
            err.detail or "-",
        )
        return (
            render_template(
                "orders/form.html",
                **_form_context(
                    role=role,
                    products=products,
                    member_id=_customer_only_member(role) or _member_for_submit(role),
                    form=request.form,
                    error=err,
                ),
            ),
            err.status,
        )
    return redirect(url_for("order.detail", order_id=created["order"]["order_id"]))


# ---------------------------------------------------------------------------
# 支付 / 取消 / 退款 / 制作状态
# ---------------------------------------------------------------------------
@bp.post("/<order_id>/pay")
@role_required("order.create")
def pay(order_id):
    order_service.pay_order(
        order_id,
        pay_method=request.form.get("pay_method"),
        pay_txn_no=request.form.get("pay_txn_no"),
        member_id=session.get("member_id"),
    )
    return redirect(url_for("order.detail", order_id=order_id))


@bp.post("/<order_id>/cancel")
@role_required("order.create")
def cancel(order_id):
    order_service.cancel_order(
        order_id,
        member_id=session.get("member_id"),
    )
    return redirect(url_for("order.detail", order_id=order_id))


@bp.post("/<order_id>/refund")
@role_required("order.refund")
def refund(order_id):
    order_service.refund_order(order_id)
    return redirect(url_for("order.detail", order_id=order_id))


@bp.post("/<order_id>/status")
@role_required("order.update_status")
def update_status(order_id):
    order_service.update_order_status(order_id, to_status=request.form.get("to_status"))
    return redirect(url_for("order.detail", order_id=order_id))
