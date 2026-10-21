"""步骤 ③ 数据访问层（DAO）自检。

运行：
    python scripts/dao_test.py

设计：
- 所有写操作都在 ``db_cursor(..., commit=False)`` 事务内执行，退出时连接关闭并回滚；
- 需要验证“持久化/回滚”的用例分两个连接执行；
- 不依赖 pytest，只用标准库 unittest。
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.dao import inventory, member, order, product, report  # noqa: E402
from app.dao._utils import normalise_limit, normalise_offset  # noqa: E402
from app.dao.connection import db_cursor, probe_database  # noqa: E402
from app.errors import AppError, classify_db_error  # noqa: E402


class DaoReadTests(unittest.TestCase):
    def test_fingerprint(self) -> None:
        health = probe_database()
        self.assertTrue(health["ok"])
        self.assertEqual(health["fingerprint"]["total"], 236)
        self.assertTrue(health["fingerprint"]["match"])

    def test_product_reads(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(len(product.list_products(cur)), 5)
            self.assertEqual(product.get_product(cur, "P0000001")["name"], "珍珠奶茶")
            self.assertEqual(len(product.list_recipe(cur, "P0000001")), 5)
            self.assertEqual(len(product.list_addon_options(cur, "P0000003")), 3)
            found = product.find_product_by_name_size(cur, "珍珠奶茶", "MEDIUM")
            self.assertIsNotNone(found)
            self.assertEqual(found["product_id"], "P0000001")
            addon = product.get_addon_option(cur, "P0000003", "M0000001")
            self.assertEqual(addon["addon_price"], Decimal("2.00"))

    def test_product_parameterized(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(product.list_products(cur, q="' OR 1=1 --"), [])
            self.assertEqual(len(product.list_products(cur, q="珍珠")), 2)

    def test_inventory_reads(self) -> None:
        with db_cursor("manager") as cur:
            materials = inventory.list_materials(cur)
            self.assertEqual(len(materials), 8)
            low = inventory.list_materials(cur, low_stock=True)
            self.assertIn("M0000008", {row["material_id"] for row in low})
            material = inventory.get_material(cur, "M0000001")
            self.assertEqual(material["stock_qty"], Decimal("4500.00"))
            self.assertEqual(material["available_qty"], Decimal("4470.00"))
            locked = inventory.get_materials_for_update(cur, ["M0000002", "M0000001"])
            self.assertEqual(set(locked), {"M0000001", "M0000002"})

    def test_order_reads(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(len(order.list_orders(cur)), 7)
            self.assertEqual(len(order.list_orders_by_member(cur, "C0000001")), 3)
            self.assertEqual(
                len(order.list_orders(cur, date_from=date(2026, 9, 16), date_to=date(2026, 9, 16))),
                7,
            )
            detail = order.get_order_detail(cur, "202609160001")
            self.assertIsNotNone(detail)
            self.assertEqual(len(detail["items"]), 2)
            self.assertEqual(len(detail["items"][1]["addons"]), 1)
            self.assertIsNone(detail["platform"])
            platform = order.get_order_platform(cur, "202609160004")
            self.assertIsNotNone(platform)
            self.assertEqual(platform["platform_name"], "MEITUAN")
            paid = order.find_order_by_pay_txn(cur, "WX202609161001")
            self.assertEqual(paid["order_id"], "202609160003")
            self.assertEqual(order.count_orders_for_day(cur, "20260916"), 7)

    def test_order_parameterized(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(order.list_orders(cur, q="' OR 1=1 --"), [])

    def test_member_reads(self) -> None:
        with db_cursor("manager") as cur:
            m = member.get_member(cur, "C0000001")
            self.assertEqual(m["points"], 118)
            self.assertEqual(m["balance"], Decimal("77.00"))
            coupons = member.list_member_coupons(cur, "C0000001")
            self.assertEqual(len(coupons), 2)
            usable = member.list_usable_coupons(cur, "C0000002", subtotal=30, today=date(2026, 10, 1))
            self.assertGreaterEqual(len(usable), 1)
            self.assertIsNotNone(member.get_coupon_rule(cur, "CP000001"))

    def test_report_queries(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(len(report.product_sales_summary(cur)), 5)
            self.assertGreaterEqual(len(report.daily_order_summary(cur)), 1)
            self.assertEqual(len(report.member_order_summary(cur)), 3)
            self.assertGreaterEqual(len(report.material_expiry_alert(cur)), 1)
            self.assertGreaterEqual(len(report.order_status_summary(cur)), 3)


class DaoWriteTests(unittest.TestCase):
    def test_product_write_and_rollback(self) -> None:
        with db_cursor("manager") as cur:
            product_id = product.next_product_id(cur)
            created = product.create_product(
                cur,
                product_id=product_id,
                name="DAO测试奶茶",
                size="MEDIUM",
                unit_price="9.90",
            )
            self.assertEqual(created["product_id"], product_id)
            self.assertTrue(product.update_product(cur, product_id, unit_price="10.50"))
            self.assertEqual(
                product.get_product(cur, product_id)["unit_price"], Decimal("10.50")
            )
        # 上一个连接退出时未 commit，应已回滚
        with db_cursor("manager") as cur:
            self.assertIsNone(product.get_product(cur, product_id))

    def test_transaction_rollback_on_exception(self) -> None:
        with db_cursor("manager") as cur:
            product_id = product.next_product_id(cur)
        # 重新进入事务，插入后主动抛异常
        with self.assertRaises(RuntimeError):
            with db_cursor("manager") as cur:
                product.create_product(
                    cur,
                    product_id=product_id,
                    name="DAO回滚测试",
                    size="SMALL",
                    unit_price="1.00",
                )
                raise RuntimeError("force rollback")
        with db_cursor("manager") as cur:
            self.assertIsNone(product.get_product(cur, product_id))

    def test_stock_actions(self) -> None:
        with db_cursor("manager") as cur:
            before = inventory.get_material(cur, "M0000001")
            self.assertEqual(inventory.reserve_stock(cur, "M0000001", 10), 1)
            reserved = inventory.get_material(cur, "M0000001")
            self.assertEqual(reserved["reserved_qty"], before["reserved_qty"] + 10)
            self.assertEqual(inventory.release_reserved(cur, "M0000001", 10), 1)
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["reserved_qty"],
                before["reserved_qty"],
            )
            self.assertEqual(inventory.confirm_reserved(cur, "M0000001", 10), 1)
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["stock_qty"],
                before["stock_qty"] - 10,
            )
            self.assertEqual(inventory.deduct_available(cur, "M0000001", 10), 1)
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["stock_qty"],
                before["stock_qty"] - 20,
            )
            # 预占不足/库存不足时返回 0
            self.assertEqual(inventory.release_reserved(cur, "M0000001", 10_000), 0)
            self.assertEqual(inventory.reserve_stock(cur, "M0000001", 10_000), 0)

    def test_adjust_stock_with_log(self) -> None:
        with db_cursor("manager") as cur:
            before = inventory.get_material(cur, "M0000001")
            after = inventory.adjust_stock_with_log(
                cur, material_id="M0000001", delta=10, employee_id="E0000003"
            )
            self.assertEqual(after["stock_qty"], before["stock_qty"] + 10)
            logs = inventory.list_stock_logs(cur, material_id="M0000001", limit=1)
            self.assertEqual(logs[0]["log_type"], "GAIN")
            self.assertEqual(logs[0]["qty"], Decimal("10.00"))

            after2 = inventory.adjust_stock_with_log(
                cur, material_id="M0000001", delta=-5, employee_id="E0000003"
            )
            self.assertEqual(after2["stock_qty"], before["stock_qty"] + 5)
            logs2 = inventory.list_stock_logs(cur, material_id="M0000001", limit=1)
            self.assertEqual(logs2[0]["log_type"], "SHORT")
            self.assertEqual(logs2[0]["qty"], Decimal("5.00"))

    def test_adjust_stock_rejects_negative(self) -> None:
        with db_cursor("manager") as cur:
            self.assertIsNone(
                inventory.adjust_stock_with_log(
                    cur, material_id="M0000001", delta=-100_000, employee_id="E0000003"
                )
            )
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["stock_qty"],
                Decimal("4500.00"),
            )

    def test_restock_flow(self) -> None:
        with db_cursor("manager") as cur:
            before = inventory.get_material(cur, "M0000001")
            created = inventory.create_restock(
                cur,
                supplier_name="DAO测试供应商",
                employee_id="E0000003",
                items=[("M0000001", 100)],
            )
            self.assertEqual(created["restock_status"], "PENDING")
            self.assertEqual(len(created["items"]), 1)

            received = inventory.receive_restock(
                cur, created["restock_id"], employee_id="E0000003"
            )
            self.assertIsNotNone(received)
            self.assertEqual(received["restock_status"], "RECEIVED")
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["stock_qty"],
                before["stock_qty"] + 100,
            )
            self.assertIsNone(
                inventory.receive_restock(
                    cur, created["restock_id"], employee_id="E0000003"
                )
            )

    def test_order_write_flow(self) -> None:
        with db_cursor("manager") as cur:
            order_id = order.next_order_id(cur, day="20261021")
            created = order.insert_order(
                cur,
                order_id=order_id,
                channel="COUNTER",
                order_mode="COUNTER_SELF",
                order_status="PENDING",
                member_id="C0000001",
                employee_id="E0000001",
                item_subtotal="12.00",
                coupon_discount="0.00",
                total_amount="12.00",
            )
            self.assertEqual(created["order_status"], "PENDING")
            order.insert_order_item(
                cur,
                order_id=order_id,
                item_line=1,
                product_id="P0000001",
                size="MEDIUM",
                qty=1,
                unit_price="12.00",
            )
            order.insert_order_addon(
                cur,
                order_id=order_id,
                item_line=1,
                material_id="M0000004",
                qty=1,
                addon_price="1.00",
            )
            detail = order.get_order_detail(cur, order_id)
            self.assertEqual(len(detail["items"]), 1)
            self.assertEqual(len(detail["items"][0]["addons"]), 1)

            self.assertEqual(
                order.update_order_payment(
                    cur,
                    order_id=order_id,
                    from_status="PENDING",
                    pay_method="ONLINE",
                    pay_txn_no="DAOTESTPAY001",
                ),
                1,
            )
            self.assertEqual(
                order.update_order_status(
                    cur, order_id=order_id, from_status="PAID", to_status="MAKING"
                ),
                1,
            )
            self.assertEqual(
                order.update_order_status(
                    cur, order_id=order_id, from_status="MAKING", to_status="COMPLETED"
                ),
                1,
            )
            self.assertIsNotNone(order.get_order(cur, order_id)["completed_at"])
            self.assertEqual(
                order.refund_order(cur, order_id=order_id, refund_amount="12.00"), 1
            )
            self.assertEqual(
                order.get_order(cur, order_id)["order_status"], "REFUNDED"
            )

    def test_platform_order_insert(self) -> None:
        with db_cursor("manager") as cur:
            order_id = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur,
                order_id=order_id,
                channel="PLATFORM",
                order_mode=None,
                order_status="PAID",
                member_id=None,
                employee_id="E0000001",
                item_subtotal="24.00",
                coupon_discount="0.00",
                total_amount="24.00",
            )
            platform = order.insert_order_platform(
                cur,
                order_id=order_id,
                platform_order_no="DAOTESTMT001",
                platform_name="MEITUAN",
                delivery_address="测试地址",
                delivery_fee="3.00",
                commission_rate="0.2000",
                commission_amount="4.80",
            )
            self.assertEqual(platform["platform_order_no"], "DAOTESTMT001")
            detail = order.get_order_detail(cur, order_id)
            self.assertIsNotNone(detail["platform"])
            self.assertEqual(detail["platform"]["platform_name"], "MEITUAN")

    def test_order_cancel(self) -> None:
        with db_cursor("manager") as cur:
            order_id = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur,
                order_id=order_id,
                channel="COUNTER",
                order_mode="COUNTER_SELF",
                order_status="PENDING",
                member_id="C0000002",
                employee_id="E0000001",
                item_subtotal="11.00",
                coupon_discount="0.00",
                total_amount="11.00",
            )
            self.assertEqual(order.cancel_order(cur, order_id), 1)
            self.assertEqual(
                order.get_order(cur, order_id)["order_status"], "CANCELLED"
            )
            self.assertEqual(order.cancel_order(cur, order_id), 0)

    def test_order_payment_guard(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(
                order.update_order_payment(
                    cur,
                    order_id="202609160001",
                    from_status="PENDING",
                    pay_method="CASH",
                ),
                0,
            )

    def test_coupon_flow(self) -> None:
        with db_cursor("manager") as cur:
            usable = member.list_member_coupons(
                cur, "C0000002", status="UNUSED"
            )
            self.assertTrue(usable)
            coupon_id = usable[0]["member_coupon_id"]

            order_id = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur,
                order_id=order_id,
                channel="COUNTER",
                order_mode="COUNTER_SELF",
                order_status="PENDING",
                member_id="C0000002",
                employee_id="E0000001",
                item_subtotal="30.00",
                coupon_discount="5.00",
                total_amount="25.00",
            )
            self.assertEqual(member.lock_coupon(cur, coupon_id, "C0000002", order_id), 1)
            self.assertEqual(
                member.get_member_coupon(cur, coupon_id)["coupon_status"], "LOCKED"
            )
            self.assertEqual(member.use_coupon(cur, coupon_id, "C0000002", order_id), 1)
            self.assertEqual(
                member.get_member_coupon(cur, coupon_id)["coupon_status"], "USED"
            )
            self.assertEqual(
                member.return_coupon(cur, coupon_id, "C0000002", order_id), 1
            )
            self.assertEqual(
                member.get_member_coupon(cur, coupon_id)["coupon_status"], "RETURNED"
            )

    def test_member_points_and_balance(self) -> None:
        with db_cursor("manager") as cur:
            before = member.get_member(cur, "C0000001")
            self.assertEqual(member.update_member_points(cur, "C0000001", 10), 1)
            self.assertEqual(member.update_member_balance(cur, "C0000001", -10), 1)
            after = member.get_member(cur, "C0000001")
            self.assertEqual(after["points"], before["points"] + 10)
            self.assertEqual(after["balance"], before["balance"] - 10)

            point_log_id = member.insert_point_log(
                cur, member_id="C0000001", point_type="EARN_SALE", points=10,
                order_id="202609160001",
            )
            balance_log_id = member.insert_balance_log(
                cur, member_id="C0000001", balance_type="SPEND", amount=10,
                order_id="202609160001",
            )
            self.assertGreater(point_log_id, 0)
            self.assertGreater(balance_log_id, 0)

    def test_constraint_error_classified(self) -> None:
        # db_cursor 在退出上下文时把 pyodbc.Error 转成 AppError，故断言要包住整个 with
        with self.assertRaises(AppError) as ctx:
            with db_cursor("manager") as cur:
                order.insert_order(
                    cur,
                    order_id="202610219999",
                    channel="COUNTER",
                    order_mode="COUNTER_SELF",
                    order_status="PAID",
                    member_id=None,
                    employee_id="E0000001",
                    item_subtotal="10.00",
                    coupon_discount="0.00",
                    total_amount="9.00",  # 违反 total_amount 等式约束
                )
        self.assertEqual(ctx.exception.code, "E5002")


class DaoGuardTests(unittest.TestCase):
    def test_negative_qty_rejected(self) -> None:
        with db_cursor("manager") as cur:
            before = inventory.get_material(cur, "M0000001")
            for func in (
                inventory.reserve_stock,
                inventory.release_reserved,
                inventory.confirm_reserved,
                inventory.deduct_available,
            ):
                with self.assertRaises(AppError) as ctx:
                    func(cur, "M0000001", -1)
                self.assertEqual(ctx.exception.code, "E1001")
            after = inventory.get_material(cur, "M0000001")
            self.assertEqual(after["stock_qty"], before["stock_qty"])
            self.assertEqual(after["reserved_qty"], before["reserved_qty"])

    def test_adjust_stock_preserves_reserved(self) -> None:
        with db_cursor("manager") as cur:
            before = inventory.get_material(cur, "M0000001")
            # 调整后 stock_qty 会低于 reserved_qty，必须拒绝且不写流水
            delta = -(before["stock_qty"] - before["reserved_qty"] + 1)
            self.assertIsNone(
                inventory.adjust_stock_with_log(
                    cur, material_id="M0000001", delta=delta, employee_id="E0000003"
                )
            )
            after = inventory.get_material(cur, "M0000001")
            self.assertEqual(after["stock_qty"], before["stock_qty"])

    def test_loss_log_type(self) -> None:
        with db_cursor("manager") as cur:
            after = inventory.adjust_stock_with_log(
                cur,
                material_id="M0000001",
                delta=-5,
                employee_id="E0000003",
                log_type="LOSS",
            )
            self.assertIsNotNone(after)
            latest = inventory.list_stock_logs(cur, material_id="M0000001", limit=1)[0]
            self.assertEqual(latest["log_type"], "LOSS")
            self.assertEqual(latest["qty"], Decimal("5.00"))

    def test_coupon_cross_order_rejected(self) -> None:
        with db_cursor("manager") as cur:
            coupon_id = member.list_member_coupons(
                cur, "C0000002", status="UNUSED"
            )[0]["member_coupon_id"]
            order_a = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur, order_id=order_a, channel="COUNTER", order_mode="COUNTER_SELF",
                order_status="PENDING", member_id="C0000002", employee_id="E0000001",
                item_subtotal="30.00", coupon_discount="5.00", total_amount="25.00",
            )
            order_b = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur, order_id=order_b, channel="COUNTER", order_mode="COUNTER_SELF",
                order_status="PENDING", member_id="C0000002", employee_id="E0000001",
                item_subtotal="30.00", coupon_discount="5.00", total_amount="25.00",
            )
            self.assertEqual(member.lock_coupon(cur, coupon_id, "C0000002", order_a), 1)
            # 对 B 单核销 A 单锁定的券必须失败
            self.assertEqual(member.use_coupon(cur, coupon_id, "C0000002", order_b), 0)
            self.assertEqual(member.return_coupon(cur, coupon_id, "C0000002", order_b), 0)
            row = member.get_member_coupon(cur, coupon_id)
            self.assertEqual(row["coupon_status"], "LOCKED")
            self.assertEqual(row["used_order_id"], order_a)
            self.assertEqual(member.return_coupon(cur, coupon_id, "C0000002", order_a), 1)

    def test_confirm_order_stock_idempotent(self) -> None:
        with db_cursor("manager") as cur:
            usages = {
                "M0000001": 30, "M0000002": 150, "M0000003": 5,
                "M0000004": 25, "M0000006": 20, "M0000007": 1,
            }
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
            order.update_order_payment(
                cur, order_id=order_id, from_status="PENDING",
                pay_method="ONLINE", pay_txn_no="IDEMPOTENT001",
            )
            before = inventory.get_material(cur, "M0000001")["stock_qty"]
            inventory.confirm_order_stock(
                cur, order_id=order_id, usages=usages, employee_id="E0000001"
            )
            mid = inventory.get_material(cur, "M0000001")["stock_qty"]
            logs_first = inventory.list_stock_logs(cur, order_id=order_id, limit=100)
            inventory.confirm_order_stock(
                cur, order_id=order_id, usages=usages, employee_id="E0000001"
            )
            after = inventory.get_material(cur, "M0000001")["stock_qty"]
            logs_second = inventory.list_stock_logs(cur, order_id=order_id, limit=100)
            self.assertEqual(mid, before - 30)
            self.assertEqual(after, mid)
            self.assertEqual(len(logs_first), len(usages))
            self.assertEqual(len(logs_second), len(usages))

    def test_deduct_order_stock_idempotent(self) -> None:
        with db_cursor("manager") as cur:
            usages = {"M0000001": 30, "M0000002": 150}
            order_id = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur, order_id=order_id, channel="PLATFORM", order_mode=None,
                order_status="PAID", member_id=None, employee_id="E0000001",
                item_subtotal="24.00", coupon_discount="0.00", total_amount="24.00",
            )
            before = inventory.get_material(cur, "M0000001")["stock_qty"]
            inventory.deduct_order_stock(
                cur, order_id=order_id, usages=usages, employee_id="E0000001"
            )
            mid = inventory.get_material(cur, "M0000001")["stock_qty"]
            inventory.deduct_order_stock(
                cur, order_id=order_id, usages=usages, employee_id="E0000001"
            )
            after = inventory.get_material(cur, "M0000001")["stock_qty"]
            logs = inventory.list_stock_logs(cur, order_id=order_id, limit=100)
            self.assertEqual(mid, before - 30)
            self.assertEqual(after, mid)
            self.assertEqual(len(logs), len(usages))

    def test_cancel_order_with_stock(self) -> None:
        with db_cursor("manager") as cur:
            usages = {"M0000001": 30, "M0000002": 150}
            before = inventory.get_material(cur, "M0000001")["reserved_qty"]
            inventory.reserve_order_stock(cur, usages)
            order_id = order.next_order_id(cur, day="20261021")
            order.insert_order(
                cur, order_id=order_id, channel="COUNTER", order_mode="COUNTER_SELF",
                order_status="PENDING", member_id="C0000001", employee_id="E0000001",
                item_subtotal="12.00", coupon_discount="0.00", total_amount="12.00",
            )
            self.assertTrue(order.cancel_order_with_stock(cur, order_id, usages))
            self.assertEqual(order.get_order(cur, order_id)["order_status"], "CANCELLED")
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["reserved_qty"], before
            )
            self.assertFalse(order.cancel_order_with_stock(cur, order_id, usages))
            self.assertEqual(
                inventory.get_material(cur, "M0000001")["reserved_qty"], before
            )

    def test_refund_and_status_guards(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(
                order.refund_order(cur, order_id="202609160005", refund_amount="13.00"), 0
            )
            self.assertEqual(
                order.get_order(cur, "202609160005")["order_status"], "PENDING"
            )
            # from_status 不匹配时条件更新必须 0 行；允许的状态机由 ④ 业务层校验
            self.assertEqual(
                order.update_order_status(
                    cur, order_id="202609160005", from_status="MAKING", to_status="COMPLETED"
                ),
                0,
            )
            self.assertEqual(
                order.get_order(cur, "202609160005")["order_status"], "PENDING"
            )

    def test_for_update_branches(self) -> None:
        with db_cursor("manager") as cur:
            self.assertIsNotNone(product.get_product(cur, "P0000001", for_update=True))
            self.assertIsNotNone(inventory.get_material(cur, "M0000001", for_update=True))
            self.assertIsNotNone(order.get_order(cur, "202609160001", for_update=True))
            self.assertIsNotNone(
                product.get_addon_option(cur, "P0000003", "M0000001", for_update=True)
            )
            self.assertIsNotNone(member.get_member(cur, "C0000001", for_update=True))
            coupons = member.list_member_coupons(cur, "C0000001", for_update=True)
            self.assertEqual(len(coupons), 2)
            self.assertIsNotNone(
                member.get_member_coupon(cur, coupons[0]["member_coupon_id"], for_update=True)
            )

    def test_points_balance_no_overdraw(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(member.update_member_points(cur, "C0000003", -1), 0)
            self.assertEqual(member.update_member_balance(cur, "C0000003", -1), 0)
            with self.assertRaises(AppError) as ctx:
                member.add_member_points(
                    cur, member_id="C0000003", delta=-1, point_type="REVERSAL",
                    order_id="202609160006",
                )
            self.assertEqual(ctx.exception.code, "E4001")

    def test_duplicate_product_2627(self) -> None:
        with self.assertRaises(AppError) as ctx:
            with db_cursor("manager") as cur:
                product.create_product(
                    cur, name="珍珠奶茶", size="MEDIUM", unit_price="12.00"
                )
        self.assertEqual(ctx.exception.code, "E5002")
        self.assertNotEqual(ctx.exception.code, "E2003")

    def test_order_detail_for_member(self) -> None:
        with db_cursor("manager") as cur:
            self.assertIsNotNone(
                order.get_order_detail_for_member(cur, "202609160002", "C0000001")
            )
            self.assertIsNone(
                order.get_order_detail_for_member(cur, "202609160005", "C0000001")
            )

    def test_usable_coupon_filters(self) -> None:
        with db_cursor("manager") as cur:
            self.assertEqual(
                member.list_usable_coupons(
                    cur, "C0000001", subtotal=30, today=date(2026, 10, 1)
                ),
                [],
            )
            self.assertEqual(
                member.list_usable_coupons(
                    cur, "C0000002", subtotal=29, today=date(2026, 10, 1)
                ),
                [],
            )
            self.assertEqual(
                member.list_usable_coupons(
                    cur, "C0000002", subtotal=30, today=date(2026, 11, 1)
                ),
                [],
            )
            self.assertEqual(
                len(member.list_usable_coupons(
                    cur, "C0000002", subtotal=30, today=date(2026, 10, 1)
                )),
                1,
            )

    def test_paired_member_helpers(self) -> None:
        with db_cursor("manager") as cur:
            before = member.get_member(cur, "C0000001")
            after = member.add_member_points(
                cur, member_id="C0000001", delta=5, point_type="EARN_SALE",
                order_id="202609160001",
            )
            self.assertEqual(after["points"], before["points"] + 5)
            after2 = member.add_member_balance(
                cur, member_id="C0000001", delta=-5, balance_type="SPEND",
                order_id="202609160001",
            )
            self.assertEqual(after2["balance"], before["balance"] - 5)

    def test_utils_normalise(self) -> None:
        self.assertEqual(normalise_limit(None), 100)
        self.assertEqual(normalise_limit(0), 100)
        self.assertEqual(normalise_limit(-1), 100)
        self.assertEqual(normalise_limit("abc"), 100)
        self.assertEqual(normalise_limit(99999), 500)
        self.assertEqual(normalise_offset(None), 0)
        self.assertEqual(normalise_offset(-5), 0)
        self.assertEqual(normalise_offset("abc"), 0)

    def test_error_classification_tx_and_unknown(self) -> None:
        self.assertEqual(classify_db_error(Exception("(1205) deadlock")).code, "E5003")
        self.assertEqual(classify_db_error(Exception("(99999) unknown")).code, "E9001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
