"""商品域业务层：字段校验、商品维护（读/写）。

职责（对应 docs/09 §2 业务层）：
  · 读操作走当前会话角色的连接，让数据库层 SELECT 权限在 DB_AUTH=sql 下兜底；
  · 写操作走 ``manager`` 连接（product.write 只有店长有），事务边界交给 DAO 闭环；
  · 只做基础校验，重复 (name, size) 等候选码冲突交给数据库唯一约束 → 2627 → E5002。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app.dao import product as _product
from app.dao.connection import db_cursor
from app.errors import AppError, ErrCode
from app.services.permissions import current_role

NAME_MAX = 100
SIZES = {"SMALL", "MEDIUM", "LARGE"}
SALE_STATUSES = {"ON_SALE", "OFF_SHELF"}


def _money(value) -> Decimal:
    """转成 DECIMAL(10,2) 金额；无法解析直接报参数错误（不落库）。"""
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"价格不合法：{value!r}") from exc


def _clean_name(value) -> str:
    name = (value or "").strip()
    if not name:
        raise AppError(ErrCode.PARAM_INVALID, "商品名不能为空。")
    if len(name) > NAME_MAX:
        raise AppError(ErrCode.PARAM_INVALID, "商品名过长。")
    return name


def _check_size(value) -> str:
    size = (value or "").strip().upper()
    if size not in SIZES:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"杯型不合法：{value!r}")
    return size


def _check_sale_status(value) -> str:
    status = (value or "ON_SALE").strip().upper()
    if status not in SALE_STATUSES:
        raise AppError(ErrCode.PARAM_INVALID, detail=f"销售状态不合法：{value!r}")
    return status


def _check_price(value) -> Decimal:
    price = _money(value)
    if price <= 0:
        raise AppError(ErrCode.PARAM_INVALID, "价格必须大于 0。")
    return price


def _map_unique_conflict(exc: AppError) -> AppError:
    """把 (name, size) 候选码冲突转成面向用户的精确提示。

    通用文案"数据不满足约束"对用户没有信息量（任务讲解要求"前端只显示面向用户的
    简明提示"）。这里只映射**唯一键冲突**（2627 / 2601 / UQ_item_product_*），
    其余约束（如 547 CHECK）保持原样，避免把"违反业务规则"误报成"重名"。
    写法与 ``order_service._is_order_id_collision`` 刻意保持同一风格。
    """
    if exc.code != ErrCode.DB_CONSTRAINT:
        return exc
    detail = exc.detail or ""
    if "2627" in detail or "2601" in detail or "UQ_item_product" in detail:
        return AppError(
            ErrCode.DB_CONSTRAINT,
            "该商品（名称 + 杯型）已存在，请改名或换杯型。",
            detail=detail,
        )
    return exc


# ---- 读 ----
def list_products(
    q: str | None = None,
    sale_status: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict]:
    """商品列表（名称模糊搜索 + 销售状态过滤 + 分页）。"""
    with db_cursor(current_role()) as cur:
        return _product.list_products(
            cur, q=q, sale_status=sale_status, limit=limit, offset=offset
        )


def get_product_detail(product_id: str) -> dict | None:
    """商品详情：基础字段 + 配方行 + 可选加料。不存在返回 None。"""
    with db_cursor(current_role()) as cur:
        detail = _product.get_product(cur, product_id)
        if detail is None:
            return None
        detail["recipe"] = _product.list_recipe(cur, product_id)
        detail["addons"] = _product.list_addon_options(cur, product_id)
        return detail


# ---- 写（manager）----
def create_product(
    name: str,
    size: str,
    unit_price,
    sale_status: str = "ON_SALE",
) -> dict:
    """新增商品；重复 (name, size) 由数据库唯一约束拒绝，并转成精确提示（E5002）。"""
    name = _clean_name(name)
    size = _check_size(size)
    unit_price = _check_price(unit_price)
    sale_status = _check_sale_status(sale_status)

    try:
        with db_cursor("manager", commit=True) as cur:
            return _product.create_product(
                cur,
                name=name,
                size=size,
                unit_price=unit_price,
                sale_status=sale_status,
            )
    except AppError as exc:
        raise _map_unique_conflict(exc) from exc


def update_product(product_id: str, **changes) -> dict:
    """按主码更新可写字段（name/size/unit_price/sale_status），返回更新后的行。"""
    product_id = (product_id or "").strip()
    if not product_id:
        raise AppError(ErrCode.PARAM_INVALID, "缺少商品编号。")

    cleaned: dict = {}
    if changes.get("name") is not None:
        cleaned["name"] = _clean_name(changes["name"])
    if changes.get("size") is not None:
        cleaned["size"] = _check_size(changes["size"])
    if changes.get("unit_price") is not None:
        cleaned["unit_price"] = _check_price(changes["unit_price"])
    if changes.get("sale_status") is not None:
        cleaned["sale_status"] = _check_sale_status(changes["sale_status"])

    if not cleaned:
        raise AppError(ErrCode.PARAM_INVALID, "没有可更新的字段。")

    # 注意：映射必须包住整段 ``with``——pyodbc 异常是在 db_cursor 退出时才被归类成
    # AppError 的，只包内层 DAO 调用会漏。（create_product 同理。）
    try:
        with db_cursor("manager", commit=True) as cur:
            if _product.get_product(cur, product_id) is None:
                raise AppError(ErrCode.NOT_FOUND)
            _product.update_product(cur, product_id, **cleaned)
            return _product.get_product(cur, product_id)
    except AppError as exc:                         # 改名/改杯型撞唯一键
        raise _map_unique_conflict(exc) from exc
