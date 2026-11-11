"""列表分页的公共小工具（页面层）。

五个列表页（订单 / 我的订单 / 商品 / 库存 / 流水 / 补货单）共用同一套口径：

  · 查询参数固定为 ``?limit=&offset=``；非法值统一 400（E1001），越界按上下界截断；
  · 翻页链接只改 ``offset``，**其它筛选条件原样保留**（否则翻页会丢筛选）；
  · 路由侧统一"多取一行"判断还有没有下一页，页面上只渲染 ``limit`` 行。

放这里而不是各自复制：口径一旦分叉（有人改成 ``page=``、有人忘记保留筛选），
翻页就会静默错位，而这类错误在页面自检里很难被发现。
"""
from __future__ import annotations

from datetime import date, datetime

from flask import request, url_for

from app.errors import AppError, ErrCode

MAX_PAGE_SIZE = 200


def int_arg(name: str, default: int, *, low: int, high: int) -> int:
    """读取整数查询参数：缺失取默认值，非整数报 400，越界截断到 [low, high]。"""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise AppError(ErrCode.PARAM_INVALID, f"{name} 必须是整数。") from None
    return max(low, min(high, value))


def date_arg(name: str) -> date | None:
    """``YYYY-MM-DD`` → date；空串返回 None；非法值按 400 处理。"""
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise AppError(ErrCode.PARAM_INVALID, f"{name} 日期格式应为 YYYY-MM-DD。") from None


def parse_paging(default: int, maximum: int = MAX_PAGE_SIZE) -> tuple[int, int]:
    """返回 ``(limit, offset)``；每页默认条数由各页自己定。"""
    limit = int_arg("limit", default, low=1, high=maximum)
    offset = int_arg("offset", 0, low=0, high=10_000_000)
    return limit, offset


def page_url(endpoint: str, **overrides) -> str:
    """翻页链接：在当前查询串基础上只改指定参数，其它筛选条件不丢。"""
    args = request.args.to_dict()
    args.update({key: str(value) for key, value in overrides.items()})
    return url_for(endpoint, **args)
