"""R02 regression. Run against a v2.1 test copy using Windows authentication."""
from uuid import uuid4

from order_pages_test import Harness


def main() -> None:
    h = Harness()
    try:
        assert h.one(
            "SELECT CONVERT(nvarchar(20), value) FROM sys.extended_properties "
            "WHERE class = 0 AND name = N'milktea_schema_version'"
        ) == "2.1"
        h.cur.execute("UPDATE dbo.item_material SET stock_qty = reserved_qty + 5000")
        h.login("customer1")
        form = {
            "channel": "COUNTER",
            "order_mode": "COUNTER_SELF",
            "qty.P0000001": "1",
            "addon.P0000001.M0000004": "1",
        }
        initial = {row[0]: (row[1], row[2]) for row in h.cur.execute(
            "SELECT material_id, stock_qty, reserved_qty FROM dbo.item_material"
        ).fetchall()}
        h.post("/orders/", form, 302)
        paid_id = h.last_order_id()
        h.post("/orders/", form, 302)
        cancel_id = h.last_order_id()
        assert paid_id and cancel_id and paid_id != cancel_id

        usage = {"M0000001": 30, "M0000002": 150, "M0000003": 5,
                 "M0000004": 25, "M0000006": 20, "M0000007": 1}
        for order_id in (paid_id, cancel_id):
            actual = dict(map(tuple, h.cur.execute(
                "SELECT material_id, qty FROM dbo.order_material_usage WHERE order_id = ?", order_id
            ).fetchall()))
            assert actual == usage, (order_id, actual, usage)
        original_total = h.order_row(paid_id)[6]
        original_price = h.one("SELECT unit_price FROM dbo.order_item WHERE order_id=?", paid_id)
        for material, (_, reserved) in initial.items():
            assert h.reserved(material) == reserved + usage.get(material, 0) * 2

        h.cur.execute("UPDATE dbo.item_product SET unit_price = unit_price + 1 WHERE product_id = 'P0000001'")
        h.cur.execute("UPDATE dbo.item_recipe SET qty = qty + 1 WHERE product_id = 'P0000001'")
        h.cur.execute("UPDATE dbo.item_addon_option SET addon_qty = addon_qty + 1 WHERE product_id = 'P0000001'")

        payment = {"pay_method": "ONLINE", "pay_txn_no": "R02-" + uuid4().hex[:16]}
        h.post(f"/orders/{paid_id}/pay", payment, 302)
        h.post(f"/orders/{paid_id}/pay", payment, 302)
        for material in initial:
            qty = usage.get(material, 0)
            assert h.stock(material) == initial[material][0] - qty
            assert h.reserved(material) == initial[material][1] + qty
        assert h.order_row(paid_id)[6] == original_total
        assert h.one("SELECT unit_price FROM dbo.order_item WHERE order_id=?", paid_id) == original_price
        logs = dict(map(tuple, h.cur.execute(
            "SELECT material_id, SUM(qty) FROM dbo.inv_stock_log "
            "WHERE order_id = ? AND log_type = 'SALE' GROUP BY material_id",
            paid_id,
        ).fetchall()))
        assert logs == usage

        h.post(f"/orders/{cancel_id}/cancel", {}, 302)
        h.post(f"/orders/{cancel_id}/cancel", {}, 409)
        for material in initial:
            qty = usage.get(material, 0)
            assert h.stock(material) == initial[material][0] - qty
            assert h.reserved(material) == initial[material][1]
        assert h.one("SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id = ?", cancel_id) == 0

        h.logout()
        h.login("maker1")
        h.post(f"/orders/{paid_id}/status", {"to_status": "MAKING"}, 302)
        h.post(f"/orders/{paid_id}/status", {"to_status": "COMPLETED"}, 302)
        h.post(f"/orders/{paid_id}/status", {"to_status": "COMPLETED"}, 409)
        assert h.order_row(paid_id)[1] == "COMPLETED"
        assert h.one("SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id = ?", paid_id) == len(usage)
        for material in initial:
            qty = usage.get(material, 0)
            assert h.stock(material) == initial[material][0] - qty
            assert h.reserved(material) == initial[material][1]

        h.logout()
        h.login("customer1")
        h.post("/orders/", form, 302)
        legacy_id = h.last_order_id()
        assert legacy_id and legacy_id not in {paid_id, cancel_id}
        h.cur.execute("DELETE dbo.order_material_usage WHERE order_id=?", legacy_id)
        before = {material: (h.stock(material), h.reserved(material)) for material in initial}
        h.post(f"/orders/{legacy_id}/pay",
               {"pay_method": "ONLINE", "pay_txn_no": "R02-" + uuid4().hex[:16]}, 409)
        h.post(f"/orders/{legacy_id}/cancel", {}, 409)
        assert h.order_row(legacy_id)[1] == "PENDING"
        assert h.one("SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id=?", legacy_id) == 0
        assert before == {material: (h.stock(material), h.reserved(material)) for material in initial}
        failures = [row for row in h.rows if row[3] != "PASS"]
        assert not failures, failures
        print("PASS: v2.1 snapshot payment, cancellation, completion and inventory isolation")
    finally:
        h.close()


if __name__ == "__main__":
    main()
