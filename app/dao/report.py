"""查询/报表 DAO：基于第 4 周统计视图的只读查询。

这些查询不修改数据；只由业务层（Service）调用，页面层不直接接触 DAO。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.dao._utils import (
    normalise_limit,
    normalise_offset,
    rows_to_dicts,
)


def product_sales_summary(
    cur,
    *,
    limit: int | None = 100,
    offset: int | None = 0,
) -> list[dict[str, Any]]:
    """商品销售汇总（售出杯数、订单数、销售额），按销售额倒序。"""
    cur.execute(
        """
        SELECT product_id, product_name, size, order_count, sold_qty, gross_amount
        FROM dbo.v_product_sales_summary
        ORDER BY gross_amount DESC, product_id
        OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
        """,
        normalise_offset(offset),
        normalise_limit(limit),
    )
    return rows_to_dicts(cur)


def daily_order_summary(
    cur,
    *,
    date_from: date | datetime | None = None,
    date_to: date | datetime | None = None,
) -> list[dict[str, Any]]:
    """每日经营统计，可按订单日期范围过滤。"""
    where: list[str] = []
    params: list[Any] = []
    if date_from:
        where.append("order_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("order_date <= ?")
        params.append(date_to)

    sql = """
        SELECT order_date, order_count, paid_order_count, refunded_order_count,
               order_amount, refund_amount, net_amount
        FROM dbo.v_daily_order_summary
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY order_date DESC"
    cur.execute(sql, *params)
    return rows_to_dicts(cur)


def material_expiry_alert(cur) -> list[dict[str, Any]]:
    """原料临期预警（按剩余天数升序）。"""
    cur.execute(
        """
        SELECT material_id, material_name, unit, shelf_life_days,
               stock_log_id, restock_in_at, estimated_expire_at,
               remaining_days, expiry_status
        FROM dbo.v_material_expiry_alert
        ORDER BY remaining_days, material_id
        """
    )
    return rows_to_dicts(cur)


def member_order_summary(cur) -> list[dict[str, Any]]:
    """会员消费汇总。"""
    cur.execute(
        """
        SELECT member_id, member_name, order_count, paid_order_count,
               paid_amount, last_order_at
        FROM dbo.v_member_order_summary
        ORDER BY paid_amount DESC, member_id
        """
    )
    return rows_to_dicts(cur)


def order_status_summary(cur) -> list[dict[str, Any]]:
    """按订单状态统计单量/金额（直接查订单表，不依赖视图）。"""
    cur.execute(
        """
        SELECT order_status, COUNT(*) AS order_count,
               SUM(total_amount) AS total_amount
        FROM dbo.shop_order
        GROUP BY order_status
        ORDER BY order_status
        """
    )
    return rows_to_dicts(cur)
