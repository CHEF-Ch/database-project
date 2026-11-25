"""Database credential lookup for the application sign-in flow."""
from app.dao._utils import fetch_one
from app.dao.connection import db_cursor, schema_version
from app.errors import AppError, ErrCode


def find_active(username: str) -> dict | None:
    with db_cursor("manager") as cur:
        if schema_version(cur) not in {"2.2", "2.3", "2.4", "2.5"}:
            raise AppError(ErrCode.DB_UNAVAILABLE, detail="USER_STORE=db requires the v2.2 account migration")
        cur.execute("EXEC dbo.usp_find_app_user @username = ?", username)
        return fetch_one(cur)
