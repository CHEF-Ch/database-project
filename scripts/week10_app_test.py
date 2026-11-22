"""Check R03 through the Flask routes on a v2 validation copy."""
import os
import sys
from decimal import Decimal
from unittest.mock import patch

from order_pages_test import Harness, PASSWORD, _SQL_LOGIN_PWD

from _csrf_client import make_client
from app import create_app
from app.config import Config
from app.dao.connection import db_cursor
from app.dao.inventory import get_material


def main() -> None:
    h = Harness()
    try:
        url = "/api/reports/sales?date_from=0001-01-01&date_to=9999-12-31"
        h.login("customer1")
        assert h.client.get(url).status_code == 403
        h.logout()
        h.login("manager1")
        response = h.client.get(url)
        assert response.status_code == 200, response.get_data(as_text=True)
        report = response.get_json()
        assert report["totals"]["order_count"] > 0
        assert sum(item["sold_qty"] for item in report["products"]) == report["totals"]["sold_qty"]
        assert h.client.get("/api/reports/sales?date_from=2026-12-01&date_to=2026-01-01").status_code == 400
        h.logout()

        h.login("stock1")
        stock = h.stock("M0000001")
        logs = h.one("SELECT COUNT(*) FROM dbo.inv_stock_log")
        h.post("/inventory/adjust", {"material_id": "M0000001", "delta": "1", "log_type": "GAIN"}, 302)
        assert h.stock("M0000001") == stock + 1
        assert h.one("SELECT COUNT(*) FROM dbo.inv_stock_log") == logs + 1
        assert h.client.get(url).status_code == 403
        h.logout()

        h.cur.execute("UPDATE dbo.item_material SET stock_qty = reserved_qty + 5000")
        rule_id = h.one("SELECT TOP (1) coupon_rule_id FROM dbo.mkt_coupon_rule ORDER BY coupon_rule_id")
        assert rule_id is not None
        h.cur.execute(
            "UPDATE dbo.mkt_coupon_rule SET threshold_amount=10, discount_amount=2, "
            "valid_from='20000101', valid_to='99991231' WHERE coupon_rule_id=?", rule_id
        )
        coupon_id = h.one(
            "INSERT dbo.mkt_member_coupon (coupon_rule_id, member_id, coupon_status, issued_at) "
            "OUTPUT inserted.member_coupon_id VALUES (?, 'C0000001', 'UNUSED', SYSDATETIME())",
            rule_id,
        )
        h.login("customer1")
        h.post("/orders/", {
            "channel": "COUNTER", "order_mode": "COUNTER_SELF", "qty.P0000001": "1",
            "member_coupon_id": str(coupon_id),
        }, 302)
        order_id = h.last_order_id()
        assert h.one("SELECT coupon_discount FROM dbo.shop_order WHERE order_id=?", order_id) == Decimal("2.00")
        assert h.one("SELECT used_order_id FROM dbo.mkt_member_coupon WHERE member_coupon_id=?",
                     coupon_id) == order_id
        failures = [row for row in h.rows if row[3] != "PASS"]
        assert not failures, failures
        print("PASS: sales reporting and permissions, stock adjustment, coupon calculation and locking")
    finally:
        h.close()


def test_sql_roles() -> None:
    Config.DB_AUTH = "sql"
    for key, value in _SQL_LOGIN_PWD.items():
        os.environ.setdefault(key, value)
    client = make_client(create_app())
    report = "/api/reports/sales?date_from=2026-09-16&date_to=2026-09-16"
    assert client.post("/login", data={"username": "manager1", "password": PASSWORD["manager1"]}).status_code == 302
    assert client.get(report).status_code == 200
    client.post("/logout")
    assert client.post("/login", data={"username": "stock1", "password": PASSWORD["stock1"]}).status_code == 302
    assert client.get(report).status_code == 403

    with db_cursor("stock") as cur:
        stock = get_material(cur, "M0000001")["stock_qty"]

    def rollback_cursor(role=None, *, commit=False):
        return db_cursor(role, commit=False)

    with patch("app.services.inventory_service.db_cursor", rollback_cursor):
        for material_id, delta, log_type, expected in (
            ("M0000001", "1", "GAIN", 302),
            ("M0000001-extra", "1", "GAIN", 400),
            ("BAD00000", "1", "GAIN", 404),
            ("M0000001", "99999999.99", "LOSS", 409),
        ):
            response = client.post("/inventory/adjust", data={
                "material_id": material_id, "delta": delta, "log_type": log_type,
            })
            assert response.status_code == expected, (material_id, response.status_code)
    with db_cursor("stock") as cur:
        assert get_material(cur, "M0000001")["stock_qty"] == stock
    print("PASS: real restricted manager, stock and app connections")


if __name__ == "__main__":
    if "--sql-roles" in sys.argv:
        test_sql_roles()
    else:
        main()
