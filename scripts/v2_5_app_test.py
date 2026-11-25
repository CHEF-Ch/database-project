"""Run authenticated, persistent end-to-end cases against the v2.5 fixture."""
import os
from time import time
from uuid import uuid4

from order_pages_test import Config, PASSWORD, _SQL_LOGIN_PWD, create_app, make_client
from app.dao.connection import db_cursor, probe_database


def login(client, username):
    assert client.post("/login", data={"username": username, "password": PASSWORD[username]}).status_code == 302


def history(coupon):
    with db_cursor("manager") as cur:
        return [
            (row.order_id, row.event_type)
            for row in cur.execute(
                "SELECT order_id, event_type FROM dbo.mkt_coupon_order_event "
                "WHERE member_coupon_id = ? ORDER BY event_id", coupon
            )
        ]


def write_state(coupon):
    with db_cursor("app") as cur:
        stock = [tuple(row) for row in cur.execute(
            "SELECT material_id, stock_qty, reserved_qty FROM dbo.item_material ORDER BY material_id"
        )]
        order_count = cur.execute("SELECT COUNT(*) FROM dbo.shop_order").fetchval()
        coupon_state = tuple(cur.execute(
            "SELECT coupon_status, used_order_id FROM dbo.mkt_member_coupon WHERE member_coupon_id = ?", coupon
        ).fetchone())
    return stock, order_count, coupon_state, history(coupon)


def check_reports_and_login(app):
    manager = make_client(app)
    assert "演示账号" not in manager.get("/login").get_data(as_text=True)
    login(manager, "manager1")
    reports = manager.get("/reports?date_from=2026-09-01&date_to=2026-09-30")
    assert reports.status_code == 200, reports.get_data(as_text=True)[-500:]
    body = reports.get_data(as_text=True)
    assert "商品销量排名" in body and "原料临期" in body and "98.00" in body
    assert manager.get("/reports?date_from=2026-01-01&date_to=2027-01-02").status_code == 400
    with manager.session_transaction() as session:
        session["last_activity"] = int(time()) - 10
    assert manager.get("/reports").status_code == 200
    with manager.session_transaction() as session:
        assert session["last_activity"] > int(time()) - 10
        session["last_activity"] = int(time()) - 1801
    assert manager.get("/reports").status_code == 401
    assert manager.post("/orders/quote", data={}).status_code == 401

    customer = make_client(app)
    login(customer, "customer1")
    assert customer.get("/reports").status_code == 403
    unknown = make_client(app)
    for _ in range(5):
        assert unknown.post("/login", data={"username": "unknown-demo", "password": "bad"}).status_code == 401
    assert unknown.post("/login", data={"username": "UNKNOWN-DEMO", "password": "bad"}).status_code == 429
    print("PASS S02 登录限流、滑动过期、过期后禁止读写、无演示账号列表")
    print("PASS F04 真实经理登录报表及日期边界，顾客拒绝访问")


def check_quote_and_history(app):
    clerk = make_client(app)
    login(clerk, "clerk1")
    with db_cursor("app") as cur:
        coupon = cur.execute(
            "SELECT member_coupon_id FROM dbo.mkt_member_coupon "
            "WHERE member_id = 'C0000002' AND coupon_status = 'UNUSED'"
        ).fetchval()
    assert coupon is not None
    order = {"qty.P0000001": "3", "channel": "COUNTER", "member_id": "C0000002"}
    before = write_state(coupon)
    low = clerk.post("/orders/quote", data={**order, "qty.P0000001": "1"})
    assert low.status_code == 200 and low.json["coupons"] == []
    quote = clerk.post("/orders/quote", data={**order, "member_coupon_id": str(coupon)})
    assert quote.status_code == 200 and quote.json["subtotal"] == "36.00", quote.json
    assert quote.json["total"] == "31.00" and any(row["id"] == coupon for row in quote.json["coupons"])
    customer = make_client(app)
    login(customer, "customer1")
    other = customer.post("/orders/quote", data={**order, "member_coupon_id": str(coupon)})
    assert other.status_code == 409
    assert write_state(coupon) == before
    print("PASS F06 报价门槛、券后金额、会员隔离及订单/库存/券状态零写入")

    orders = []
    for _ in range(2):
        created = clerk.post("/orders/", data={**order, "member_coupon_id": str(coupon)})
        assert created.status_code == 302, created.status_code
        orders.append(created.location.rsplit("/", 1)[-1])
        if len(orders) == 1:
            assert clerk.post(f"/orders/{orders[-1]}/cancel").status_code == 302
    payment = {"pay_method": "ONLINE", "pay_txn_no": f"R08-{uuid4().hex[:20]}"}
    for _ in range(2):
        assert clerk.post(f"/orders/{orders[-1]}/pay", data=payment).status_code == 302
    assert history(coupon)[-4:] == [
        (orders[0], "LOCKED"), (orders[0], "RETURNED"),
        (orders[1], "LOCKED"), (orders[1], "USED"),
    ]
    with db_cursor("app") as cur:
        assert str(cur.execute("SELECT total_amount FROM dbo.shop_order WHERE order_id = ?", orders[-1]).fetchval()) == quote.json["total"]
    manager = make_client(app)
    login(manager, "manager1")
    assert manager.post(f"/orders/{orders[-1]}/refund").status_code == 302
    assert manager.post(f"/orders/{orders[-1]}/refund").status_code == 409
    assert history(coupon)[-5:] == [
        (orders[0], "LOCKED"), (orders[0], "RETURNED"),
        (orders[1], "LOCKED"), (orders[1], "USED"), (orders[1], "RETURNED"),
    ]
    print("PASS R08 锁券、取消归还、复用、核销与退款归还，重复支付和退款无重复事件")


if __name__ == "__main__":
    assert Config.DB_NAME == "MilkTeaShop_week11_refactor", "必须使用独立 v2.5 验收库"
    Config.DB_AUTH = "sql"
    for name, password in _SQL_LOGIN_PWD.items():
        os.environ.setdefault(name, password)
    health = probe_database()
    assert health["schema_version"] == "2.5" and health["fingerprint"]["match"], health
    application = create_app()
    check_reports_and_login(application)
    check_quote_and_history(application)
