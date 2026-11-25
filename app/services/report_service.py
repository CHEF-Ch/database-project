"""Application entry point for the v2 sales statistics object."""
from datetime import date

from app.dao import report
from app.dao.connection import db_cursor, schema_version
from app.errors import AppError, ErrCode


def sales_statistics(date_from: date | None, date_to: date | None) -> dict:
    if date_from is None or date_to is None or date_from > date_to:
        raise AppError(ErrCode.PARAM_INVALID, "请输入有效的开始和结束日期。")

    with db_cursor("manager") as cur:
        if schema_version(cur) not in {"2.0", "2.1", "2.2", "2.3", "2.4", "2.5"}:
            raise AppError(ErrCode.BUSINESS_RULE, "销售统计需要先部署 v2 数据库迁移。")
        return report.sales_statistics(cur, date_from, date_to)


def dashboard(date_from: date, date_to: date) -> dict:
    if not 0 <= (date_to - date_from).days <= 365:
        raise AppError(ErrCode.PARAM_INVALID, "报表区间最多为 366 天，结束日期不得早于开始日期。")
    with db_cursor("manager") as cur:
        if schema_version(cur) != "2.5":
            raise AppError(ErrCode.BUSINESS_RULE, "请先部署 v2.5 报表授权。")
        return {
            **report.sales_statistics(cur, date_from, date_to),
            **report.sales_windows(cur, date_from, date_to),
            "expiry": report.material_expiry_alert(cur),
        }
