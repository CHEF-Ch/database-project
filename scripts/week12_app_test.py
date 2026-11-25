"""在独立 v2.4 验收库验证真实 SQL 登录的补货与支付回滚。

设置 DB_NAME、DB_AUTH=sql、库存、写事务及经理登录密码后从仓库根目录运行。
每次创建一张新补货单并提交，运行后可在验收库查看流水。
"""
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config
from app.dao.connection import db_cursor, probe_database
from app.errors import AppError, ErrCode
from app.services.inventory_service import create_restock, receive_restock
from app.services.order_service import cancel_order, create_order, pay_order

MATERIAL = "M0000002"
EMPLOYEE = "E0000003"
MEMBER = "C0000002"


def read_stock() -> Decimal:
    with db_cursor("stock") as cur:
        return cur.execute(
            "SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", MATERIAL
        ).fetchval()


def check_restock() -> None:
    before = read_stock()
    restock = create_restock("第十二周应用验收", EMPLOYEE, [(MATERIAL, 5)])
    restock_id = restock["restock_id"]
    received = receive_restock(restock_id, EMPLOYEE)
    if received["restock_status"] != "RECEIVED" or read_stock() != before + 5:
        raise AssertionError("验收状态或跨连接读取的库存不符。")

    try:
        receive_restock(restock_id, EMPLOYEE)
    except AppError as exc:
        if exc.code != ErrCode.BUSINESS_RULE:
            raise
    else:
        raise AssertionError("重复验收应返回业务错误。")

    with db_cursor("stock") as cur:
        count = cur.execute(
            "SELECT COUNT(*) FROM dbo.inv_stock_log WHERE restock_id = ? AND log_type = 'IN'",
            restock_id,
        ).fetchval()
    after = read_stock()
    if count != 1 or after != before + 5:
        raise AssertionError("重复验收改变了库存或业务流水。")
    print(f"PASS v2.4 SQL 登录补货应用：{restock_id}，库存 {before} → {after}，IN 流水 {count} 条。")


def payment_state(order_id: str) -> tuple:
    with db_cursor("app") as cur:
        materials = cur.execute(
            "SELECT u.material_id, m.stock_qty, m.reserved_qty "
            "FROM dbo.order_material_usage AS u JOIN dbo.item_material AS m "
            "ON m.material_id = u.material_id WHERE u.order_id = ? ORDER BY u.material_id",
            order_id,
        ).fetchall()
        order = cur.execute(
            "SELECT order_status, paid_at, pay_method, pay_txn_no "
            "FROM dbo.shop_order WHERE order_id = ?", order_id
        ).fetchone()
        sales = cur.execute(
            "SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id = ? AND log_type = 'SALE'",
            order_id,
        ).fetchval()
    with db_cursor("stock") as cur:
        audits = cur.execute("SELECT COUNT_BIG(*) FROM dbo.inv_stock_audit").fetchval()
    return [tuple(row) for row in materials], tuple(order), sales, audits


def check_payment_rollback() -> None:
    order = create_order(
        items=[{
            "product_id": "P0000004", "qty": 2, "sugar_level": "HALF",
            "ice_level": "LESS", "temp_level": "COLD",
        }],
        role="customer", member_id=MEMBER,
    )["order"]
    order_id = order["order_id"]
    materials = payment_state(order_id)[0]
    if len(materials) < 2:
        raise AssertionError("支付回滚夹具至少需要两种原料。")
    material_id, _, reserved = materials[-1]
    try:
        with db_cursor("stock", commit=True) as cur:
            cur.execute("UPDATE dbo.item_material SET reserved_qty = 0 WHERE material_id = ?", material_id)
        before = payment_state(order_id)
        try:
            pay_order(
                order_id, pay_method="ONLINE", pay_txn_no=f"W12-{uuid4().hex[:20]}",
                role="customer", member_id=MEMBER,
            )
        except AppError as exc:
            if exc.code != ErrCode.BUSINESS_RULE or exc.extra.get("material_id") != material_id:
                raise
        else:
            raise AssertionError("后续原料预占不足时支付应失败。")
        if payment_state(order_id) != before or before[1] != ("PENDING", None, None, None) or before[2] != 0:
            raise AssertionError("支付失败后订单、库存、预占、SALE 流水或审计发生变化。")
    finally:
        with db_cursor("stock", commit=True) as cur:
            cur.execute(
                "UPDATE dbo.item_material SET reserved_qty = ? WHERE material_id = ?", reserved, material_id
            )
        cancel_order(order_id, role="customer", member_id=MEMBER)
    print("PASS 后续原料预占不足，支付拒绝，先前扣料、SALE 流水及审计均已回滚。")


def main() -> None:
    if not Config.DB_NAME.startswith("MilkTeaShop_week11_week12") or Config.DB_AUTH != "sql":
        raise SystemExit("请使用独立第十二周验收库和 SQL 登录。")
    health = probe_database()
    if health["schema_version"] != "2.4" or not health["fingerprint"]["match"]:
        raise SystemExit("v2.4 数据库结构指纹不匹配。")
    check_restock()
    check_payment_rollback()


if __name__ == "__main__":
    main()
