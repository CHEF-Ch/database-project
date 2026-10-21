r"""按受限 SQL 登录验证 DAO 的角色路径（DB_AUTH=sql）。

运行：
    python scripts/dao_roles_test.py

前置：
    sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/app_login.sql

说明：
- 使用 sql/app_login.sql 中的示例密码；真实环境请用环境变量覆盖。
- 所有写操作在事务内执行并回滚，不污染 MilkTeaShop_v01。
- 顾客连接只验证“下单预占 + 只看自己的单”；支付确认/扣料由 clerk/系统连接执行。
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

# 必须在导入 app.config 之前设置，Config 的类属性在 import 时求值。
os.environ.setdefault("DB_AUTH", "sql")
os.environ.setdefault("DB_PWD_LOGIN_CLERK", "Clerk#2026")
os.environ.setdefault("DB_PWD_LOGIN_CUSTOMER", "Cust#2026")
os.environ.setdefault("DB_PWD_LOGIN_MAKER", "Maker#2026")
os.environ.setdefault("DB_PWD_LOGIN_STOCK", "Stock#2026")
os.environ.setdefault("DB_PWD_LOGIN_MANAGER", "Mgr#2026")
os.environ.setdefault("DB_PWD_LOGIN_APP", "App#2026")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.dao import inventory, member, order  # noqa: E402
from app.dao.connection import db_cursor  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        RESULTS.append((name, True, ""))
    except Exception as exc:  # noqa: BLE001 - 自检脚本要记录所有失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))


def maker_paths() -> None:
    with db_cursor("maker") as cur:
        assert len(order.get_order_detail(cur, "202609160001")["items"]) == 2
        assert order.get_order_detail(cur, "202609160004")["platform"]["platform_name"] == "MEITUAN"
        assert order.update_order_status(
            cur, order_id="202609160005", from_status="PENDING", to_status="MAKING"
        ) == 1
        assert order.update_order_status(
            cur, order_id="202609160001", from_status="COMPLETED", to_status="COMPLETED"
        ) == 1


def clerk_read_paths() -> None:
    with db_cursor("clerk") as cur:
        assert len(order.get_order_detail(cur, "202609160001")["items"]) == 2
        assert order.update_order_status(
            cur, order_id="202609160005", from_status="PENDING", to_status="MAKING"
        ) == 1


def clerk_order_flow() -> None:
    usages = {
        "M0000001": 30, "M0000002": 150, "M0000003": 5,
        "M0000004": 25, "M0000006": 20, "M0000007": 1,
    }
    with db_cursor("app") as cur:
        inventory.reserve_order_stock(cur, usages)
        order_id = order.next_order_id(cur, day="20261021")
        order.insert_order(
            cur, order_id=order_id, channel="COUNTER", order_mode="COUNTER_SELF",
            order_status="PENDING", member_id="C0000001", employee_id="E0000001",
            item_subtotal="13.00", coupon_discount="0.00", total_amount="13.00",
        )
        order.insert_order_item(
            cur, order_id=order_id, item_line=1, product_id="P0000001",
            size="MEDIUM", qty=1, unit_price="12.00",
        )
        order.insert_order_addon(
            cur, order_id=order_id, item_line=1, material_id="M0000004",
            qty=1, addon_price="1.00",
        )
        assert order.update_order_payment(
            cur, order_id=order_id, from_status="PENDING",
            pay_method="ONLINE", pay_txn_no="ROLEPAY001",
        ) == 1
        inventory.confirm_order_stock(
            cur, order_id=order_id, usages=usages, employee_id="E0000001"
        )
        assert len(inventory.list_stock_logs(cur, order_id=order_id, limit=100)) == len(usages)


def clerk_platform_order() -> None:
    with db_cursor("app") as cur:
        order_id = order.next_order_id(cur, day="20261021")
        order.insert_order(
            cur, order_id=order_id, channel="PLATFORM", order_mode=None,
            order_status="PAID", member_id=None, employee_id="E0000001",
            item_subtotal="24.00", coupon_discount="0.00", total_amount="24.00",
        )
        platform = order.insert_order_platform(
            cur, order_id=order_id, platform_order_no="ROLEMT001",
            platform_name="MEITUAN", delivery_address="测试地址",
            delivery_fee="3.00", commission_rate="0.2000", commission_amount="4.80",
        )
        assert platform["platform_order_no"] == "ROLEMT001"


def customer_scope() -> None:
    with db_cursor("customer") as cur:
        assert order.get_order_detail_for_member(
            cur, "202609160002", "C0000001"
        ) is not None
        assert order.get_order_detail_for_member(
            cur, "202609160005", "C0000001"
        ) is None
        assert inventory.reserve_stock(cur, "M0000001", 1) == 1

    # 顾客没有扣料/流水权限：支付确认阶段由 clerk/系统连接执行
    try:
        with db_cursor("customer") as cur:
            inventory.confirm_reserved(cur, "M0000001", 1)
    except AppError as exc:
        assert exc.code == "E2003"
    else:
        raise AssertionError("customer 不应有 confirm_reserved 权限")


def stock_path() -> None:
    with db_cursor("stock") as cur:
        before = inventory.get_material(cur, "M0000001")["stock_qty"]
        after = inventory.adjust_stock_with_log(
            cur, material_id="M0000001", delta=5, employee_id="E0000003"
        )
        assert after["stock_qty"] == before + 5


def manager_member_path() -> None:
    with db_cursor("manager") as cur:
        before = member.get_member(cur, "C0000001")
        after = member.add_member_points(
            cur, member_id="C0000001", delta=5, point_type="EARN_SALE",
            order_id="202609160001",
        )
        assert after["points"] == before["points"] + 5
        after2 = member.add_member_balance(
            cur, member_id="C0000001", delta=Decimal("-5"), balance_type="SPEND",
            order_id="202609160001",
        )
        assert after2["balance"] == before["balance"] - 5


def main() -> int:
    check("maker: 订单详情/平台详情/改状态", maker_paths)
    check("clerk: 订单详情/改状态", clerk_read_paths)
    check("app: 下单→支付→扣料→流水", clerk_order_flow)
    check("app: 平台订单写入", clerk_platform_order)
    check("customer: 只看自己的单 + 仅预占", customer_scope)
    check("stock: 库存调整 + 流水", stock_path)
    check("manager: 积分/余额 + 流水", manager_member_path)

    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    print(f"\nRESULT: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
