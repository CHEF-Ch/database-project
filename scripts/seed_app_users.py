"""Opt-in demonstration accounts for a v2.2 sandbox."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dao.connection import db_cursor, schema_version
from app.demo_users import DEMO_USERS


def main() -> None:
    with db_cursor(commit=True) as cur:
        cur.execute("SELECT DB_NAME()")
        if cur.fetchone()[0] in {"MilkTeaShop", "MilkTeaShop_v01"} or schema_version(cur) != "2.2":
            raise SystemExit("Select a v2.2 sandbox with DB_NAME.")
        for username, account in DEMO_USERS.items():
            cur.execute(
                """
                INSERT dbo.app_user (username, password_hash, role_code, member_id, employee_id, display_name)
                SELECT ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM dbo.app_user WITH (UPDLOCK, HOLDLOCK) WHERE username = ?)
                """,
                username, account["pwd"], account["role"], account.get("member_id"),
                account.get("employee_id"), account["display"], username,
            )
    print("Demo account setup complete.")


if __name__ == "__main__":
    main()
