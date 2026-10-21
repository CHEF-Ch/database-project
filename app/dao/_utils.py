"""DAO 层内部小工具：结果映射与分页参数校验。

只做纯函数，不碰连接/事务；所有 DAO 模块共用。
"""
from __future__ import annotations

from typing import Any


def rows_to_dicts(cur) -> list[dict[str, Any]]:
    """把当前游标 fetchall() 的结果按列名转成字典列表。"""
    columns = [item[0] for item in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def row_to_dict(cur, row) -> dict[str, Any] | None:
    """把单行结果按列名转成字典；row 为 None 时返回 None。"""
    if row is None:
        return None
    columns = [item[0] for item in cur.description]
    return dict(zip(columns, row))


def fetch_one(cur) -> dict[str, Any] | None:
    """读取一行并按列名转字典。"""
    return row_to_dict(cur, cur.fetchone())


def fetch_scalar(cur, default: Any = None) -> Any:
    """读取单行单列；没有行时返回 default。"""
    row = cur.fetchone()
    if row is None:
        return default
    return row[0]


def normalise_limit(limit: int | None, *, default: int = 100, maximum: int = 500) -> int:
    """分页 limit 统一校验：None/非正数回退默认值，超过上限截断。"""
    if limit is None:
        return default
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, maximum)


def normalise_offset(offset: int | None) -> int:
    """分页 offset 统一校验：None/负数归零。"""
    if offset is None:
        return 0
    try:
        return max(0, int(offset))
    except (TypeError, ValueError):
        return 0


def placeholders(count: int) -> str:
    """生成 ``?, ?, ?`` 形式的占位符串（仅用于参数个数，不拼用户输入）。"""
    if count <= 0:
        raise ValueError("placeholders() 需要正整数")
    return ", ".join("?" for _ in range(count))
