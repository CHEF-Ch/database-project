"""商品域 DAO：商品、配方、商品-加料选项。

所有函数都接收一个已打开的游标（``cur``），由业务层决定连接与事务边界。
SQL 全部使用 ``?`` 参数绑定，禁止把用户输入拼进语句。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.dao._utils import (
    fetch_one,
    fetch_scalar,
    normalise_limit,
    normalise_offset,
    rows_to_dicts,
)
from app.errors import AppError, ErrCode

_PRODUCT_COLUMNS = "product_id, name, size, unit_price, sale_status"


def _money(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def list_products(
    cur,
    *,
    q: str | None = None,
    sale_status: str | None = None,
    limit: int | None = 100,
    offset: int | None = 0,
) -> list[dict[str, Any]]:
    """商品列表，可按名称模糊搜索、销售状态过滤，分页返回。"""
    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append("name LIKE ?")
        params.append(f"%{q}%")
    if sale_status:
        where.append("sale_status = ?")
        params.append(sale_status)

    sql = f"SELECT {_PRODUCT_COLUMNS} FROM dbo.item_product"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY product_id OFFSET ? ROWS FETCH NEXT ? ROWS ONLY"
    params.extend([normalise_offset(offset), normalise_limit(limit)])

    cur.execute(sql, *params)
    return rows_to_dicts(cur)


def get_product(cur, product_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    """按主码取商品；``for_update=True`` 时加行锁，供下单/改价事务使用。"""
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    cur.execute(
        f"SELECT {_PRODUCT_COLUMNS} FROM dbo.item_product{lock} WHERE product_id = ?",
        product_id,
    )
    return fetch_one(cur)


def find_product_by_name_size(cur, name: str, size: str) -> dict[str, Any] | None:
    """按候选码 ``(name, size)`` 查商品，用于重复校验。"""
    cur.execute(
        f"SELECT {_PRODUCT_COLUMNS} FROM dbo.item_product WHERE name = ? AND size = ?",
        name,
        size,
    )
    return fetch_one(cur)


def next_product_id(cur) -> str:
    """生成下一个商品主码：``P`` + 7 位序号。

    使用 ``UPDLOCK, HOLDLOCK`` 在事务内锁住范围，避免并发取到同一个号。
    调用方必须在事务中执行；序号超过 7 位时抛 ValueError。
    """
    cur.execute(
        "SELECT ISNULL(MAX(TRY_CAST(SUBSTRING(product_id, 2, 7) AS int)), 0) + 1 "
        "FROM dbo.item_product WITH (UPDLOCK, HOLDLOCK)"
    )
    number = int(fetch_scalar(cur, 1))
    if number > 9_999_999:
        raise AppError(ErrCode.BUSINESS_RULE, detail="商品主码序号已超过 7 位")
    return f"P{number:07d}"


def create_product(
    cur,
    *,
    name: str,
    size: str,
    unit_price: Decimal | int | float,
    sale_status: str = "ON_SALE",
    product_id: str | None = None,
) -> dict[str, Any]:
    """新增商品；未传 ``product_id`` 时自动取下一个主码。"""
    product_id = product_id or next_product_id(cur)
    cur.execute(
        """
        INSERT INTO dbo.item_product (product_id, name, size, unit_price, sale_status)
        OUTPUT INSERTED.product_id, INSERTED.name, INSERTED.size,
               INSERTED.unit_price, INSERTED.sale_status
        VALUES (?, ?, ?, ?, ?)
        """,
        product_id,
        name,
        size,
        _money(unit_price),
        sale_status,
    )
    created = fetch_one(cur)
    if created is None:
        raise AppError(ErrCode.INTERNAL, detail="商品插入后未返回行")
    return created


def update_product(cur, product_id: str, **changes: Any) -> bool:
    """按主码更新商品的可写字段，返回是否命中一行。

    白名单字段：``name`` / ``size`` / ``unit_price`` / ``sale_status``。
    未提供任何可写字段或值全为 None 时返回 False。
    """
    allowed = ("name", "size", "unit_price", "sale_status")
    fields: list[str] = []
    params: list[Any] = []
    for key in allowed:
        if key not in changes or changes[key] is None:
            continue
        fields.append(f"{key} = ?")
        value = changes[key]
        params.append(_money(value) if key == "unit_price" else value)

    if not fields:
        return False

    params.append(product_id)
    cur.execute(
        f"UPDATE dbo.item_product SET {', '.join(fields)} WHERE product_id = ?",
        *params,
    )
    return cur.rowcount == 1


def list_recipe(cur, product_id: str) -> list[dict[str, Any]]:
    """取某商品的配方行（原料 + 每杯标准用量）。"""
    cur.execute(
        """
        SELECT r.material_id, m.name AS material_name, m.unit,
               m.material_kind, r.qty
        FROM dbo.item_recipe AS r
        JOIN dbo.item_material AS m ON m.material_id = r.material_id
        WHERE r.product_id = ?
        ORDER BY r.material_id
        """,
        product_id,
    )
    return rows_to_dicts(cur)


def list_addon_options(cur, product_id: str) -> list[dict[str, Any]]:
    """取某商品可选的加料（含加料单价、每份用量、上下架状态）。"""
    cur.execute(
        """
        SELECT a.product_id, a.material_id, m.name AS material_name, m.unit,
               a.addon_price, a.addon_qty, a.addon_status
        FROM dbo.item_addon_option AS a
        JOIN dbo.item_material AS m ON m.material_id = a.material_id
        WHERE a.product_id = ?
        ORDER BY a.material_id
        """,
        product_id,
    )
    return rows_to_dicts(cur)


def get_addon_option(
    cur,
    product_id: str,
    material_id: str,
    *,
    for_update: bool = False,
) -> dict[str, Any] | None:
    """取某商品的某个加料选项；下单时用于校验加料归属并取价。"""
    lock = " WITH (UPDLOCK, ROWLOCK)" if for_update else ""
    cur.execute(
        f"""
        SELECT a.product_id, a.material_id, m.name AS material_name, m.unit,
               m.material_kind, a.addon_price, a.addon_qty, a.addon_status
        FROM dbo.item_addon_option AS a{lock}
        JOIN dbo.item_material AS m ON m.material_id = a.material_id
        WHERE a.product_id = ? AND a.material_id = ?
        """,
        product_id,
        material_id,
    )
    return fetch_one(cur)
