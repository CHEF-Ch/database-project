"""S01 acceptance checks against a seeded v2.2 sandbox."""
from unittest.mock import patch

from order_pages_test import PASSWORD
from _csrf_client import make_client
from app import create_app
from app.config import Config
from app.dao.connection import db_cursor
from app.demo_users import DEMO_USERS
from app.services.auth import DbUserStore


def main() -> None:
    Config.USER_STORE = "db"
    client = make_client(create_app())
    health = client.get("/health").get_json()
    assert health["schema_version"] == "2.2" and health["fingerprint"]["match"], health
    login_page = client.get("/login").get_data(as_text=True)
    assert "clerk1" not in login_page
    for username, account in DEMO_USERS.items():
        result = client.post("/login", data={"username": username, "password": PASSWORD[username]})
        assert result.status_code == 302, (username, result.status_code)
        with client.session_transaction() as session:
            for key in ("role", "employee_id", "member_id"):
                assert session.get(key) == account.get(key), (username, key)
        assert client.post("/logout").status_code == 302

    for username, password in (("customer1", "invalid"), ("CUSTOMER1", PASSWORD["customer1"])):
        result = client.post("/login", data={"username": username, "password": password})
        assert result.status_code == 401
    with db_cursor() as cur:
        cur.execute("UPDATE dbo.app_user SET is_active = 0 WHERE username = 'customer1'")
        cur.execute("UPDATE dbo.staff_employee SET employ_status = 'RESIGNED' WHERE employee_id = 'E0000001'")

        from contextlib import nullcontext

        with patch("app.dao.user.db_cursor", return_value=nullcontext(cur)):
            assert DbUserStore().verify("customer1", PASSWORD["customer1"]) is None
            assert DbUserStore().verify("clerk1", PASSWORD["clerk1"]) is None
    assert DbUserStore().verify("customer1", PASSWORD["customer1"]) is not None
    assert DbUserStore().verify("clerk1", PASSWORD["clerk1"]) is not None
    print("PASS: health, five roles and bindings, wrong password, case, account and staff suspension")


if __name__ == "__main__":
    main()
