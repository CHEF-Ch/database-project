"""Application entry point for the v2 sales statistics object."""
from datetime import date

from app.dao import report
from app.dao.connection import db_cursor, schema_version
from app.errors import AppError, ErrCode


def sales_statistics(date_from: date | None, date_to: date | None) -> dict:
    if date_from is None or date_to is None or date_from > date_to:
        raise AppError(ErrCode.PARAM_INVALID, "请输入有效的开始和结束日期。")

    with db_cursor("manager") as cur:
        if schema_version(cur) not in {"2.0", "2.1"}:
            raise AppError(ErrCode.BUSINESS_RULE, "销售统计需要先部署 v2 数据库迁移。")
        return report.sales_statistics(cur, date_from, date_to)
