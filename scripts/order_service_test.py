"""④ 订单业务层自检：在**一个外层事务里跑完再整体回滚**，不污染 seed 数据。

做法：把 ``order_service.db_cursor`` 换成一个"同一个连接/游标 + SQL Server
SAVE TRANSACTION"的假上下文：
  · 每次服务调用 = 一个 savepoint；
  · 服务抛异常 → 回滚到该 savepoint（等价于真实 db_cursor 的事务回滚）；
  · 服务成功且 commit=True → 保留改动供断言；
  · 全部用例结束 → 外层 conn.rollback()，数据库回到运行前状态。

运行：python scripts/order_service_test.py
"""
from __future__ import annotations

import re
import sys
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.dao.connection import connect
import app.services.order_service as svc
from app.errors import AppError

# 种子券 CP000001 的有效期是 2026-09-01—2026-10-31；固定 today，避免用例随时间失效。
FIXED_DAY = date(2026, 10, 25)


def _d(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class OrderServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.conn = connect(None)
        cls.cur = cls.conn.cursor()
        cls._orig_db_cursor = svc.db_cursor

        @contextmanager
        def fake_db_cursor(role=None, commit=False):
            cls.cur.execute("SAVE TRANSACTION sp_call")
            try:
                yield cls.cur
            except BaseException:
                cls.cur.execute("ROLLBACK TRANSACTION sp_call")
                raise
            else:
                if not commit:
                    cls.cur.execute("ROLLBACK TRANSACTION sp_call")

        svc.db_cursor = fake_db_cursor

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.conn.rollback()
        finally:
            cls.cur.close()
            cls.conn.close()
            svc.db_cursor = cls._orig_db_cursor

    def setUp(self) -> None:
        self.cur.execute("SAVE TRANSACTION sp_test")

    def tearDown(self) -> None:
        self.cur.execute("ROLLBACK TRANSACTION sp_test")

    # ---- 小工具 ---------------------------------------------------------
    def scalar(self, sql: str, *params):
        self.cur.execute(sql, *params)
        return self.cur.fetchone()[0]

    def stock(self, material_id: str) -> Decimal:
        return _d(self.scalar("SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", material_id))

    def reserved(self, material_id: str) -> Decimal:
        return _d(self.scalar("SELECT reserved_qty FROM dbo.item_material WHERE material_id = ?", material_id))

    def points(self, member_id: str) -> int:
        return int(self.scalar("SELECT points FROM dbo.member_member WHERE member_id = ?", member_id))

    def balance(self, member_id: str) -> Decimal:
        return _d(self.scalar("SELECT balance FROM dbo.member_member WHERE member_id = ?", member_id))

    def order_status(self, order_id: str) -> str:
        return str(self.scalar("SELECT order_status FROM dbo.shop_order WHERE order_id = ?", order_id))

    def coupon_status(self, coupon_id: int) -> str:
        return str(self.scalar("SELECT coupon_status FROM dbo.mkt_member_coupon WHERE member_coupon_id = ?", coupon_id))

    def coupon_id(self, member_id: str, status: str = "UNUSED") -> int:
        self.cur.execute(
            "SELECT TOP 1 member_coupon_id FROM dbo.mkt_member_coupon "
            "WHERE member_id = ? AND coupon_status = ? ORDER BY member_coupon_id",
            member_id,
            status,
        )
        row = self.cur.fetchone()
        self.assertIsNotNone(row, f"{member_id} 没有 {status} 状态的券")
        return int(row[0])

    def used_order_id(self, coupon_id: int) -> str | None:
        return self.scalar(
            "SELECT used_order_id FROM dbo.mkt_member_coupon WHERE member_coupon_id = ?",
            coupon_id,
        )

    def sale_qty(self, order_id: str, material_id: str) -> Decimal:
        self.cur.execute(
            "SELECT ISNULL(SUM(qty), 0) FROM dbo.inv_stock_log "
            "WHERE order_id = ? AND material_id = ? AND log_type = 'SALE'",
            order_id,
            material_id,
        )
        return _d(self.cur.fetchone()[0])

    def assert_app_error(self, code: str, func, *args, **kwargs) -> None:
        with self.assertRaises(AppError) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def base_item(self, product_id="P0000003", qty=1, addons=None) -> dict:
        return {"product_id": product_id, "qty": qty, "addons": addons or []}

    # ---- 下单：现金到店 -------------------------------------------------
    def test_cash_order_is_paid_and_deducts_stock(self):
        before_stock = self.stock("M0000001")
        before_reserved = self.reserved("M0000001")

        detail = svc.create_order(
            items=[self.base_item("P0000001", 1, [{"material_id": "M0000004", "qty": 1}])],
            role="clerk",
            employee_id="E0000001",
            pay_method="CASH",
        )
        order = detail["order"]
        self.assertEqual(order["order_status"], "PAID")
        self.assertEqual(order["channel"], "COUNTER")
        self.assertEqual(order["order_mode"], "COUNTER_CASHIER")
        self.assertEqual(order["pay_method"], "CASH")
        self.assertIsNotNone(order["paid_at"])
        self.assertEqual(_d(order["item_subtotal"]), _d("13.00"))
        self.assertEqual(_d(order["total_amount"]), _d("13.00"))
        self.assertRegex(order["order_id"], r"^\d{12}$")

        # 珍珠配方 30；椰果加料 25；净扣现存，预占回到原值
        self.assertEqual(self.stock("M0000001"), before_stock - _d("30"))
        self.assertEqual(self.reserved("M0000001"), before_reserved)
        self.assertEqual(self.sale_qty(order["order_id"], "M0000001"), _d("30"))
        self.assertEqual(self.sale_qty(order["order_id"], "M0000004"), _d("25"))

        item = detail["items"][0]
        self.assertEqual(item["size"], "MEDIUM")
        self.assertEqual(_d(item["unit_price"]), _d("12.00"))
        self.assertEqual(_d(item["addons"][0]["addon_price"]), _d("1.00"))

    # ---- 下单：自助 PENDING → 支付 -------------------------------------
    def test_self_order_pending_reserves_then_pay(self):
        before_stock = self.stock("M0000001")
        before_reserved = self.reserved("M0000001")
        before_points = self.points("C0000001")

        detail = svc.create_order(
            items=[self.base_item("P0000003", 1, [{"material_id": "M0000001", "qty": 1}])],
            role="customer",
            member_id="C0000001",
        )
        order = detail["order"]
        self.assertEqual(order["order_status"], "PENDING")
        self.assertEqual(order["order_mode"], "COUNTER_SELF")
        self.assertIsNone(order["pay_method"])
        self.assertIsNone(order["paid_at"])
        self.assertEqual(_d(order["total_amount"]), _d("10.00"))
        self.assertEqual(self.reserved("M0000001"), before_reserved + _d("30"))
        self.assertEqual(self.stock("M0000001"), before_stock)
        self.assertEqual(self.sale_qty(order["order_id"], "M0000001"), _d("0"))

        paid = svc.pay_order(
            order["order_id"],
            pay_method="ONLINE",
            pay_txn_no="TEST-WX-0001",
            role="customer",
            member_id="C0000001",
        )
        self.assertEqual(paid["order"]["order_status"], "PAID")
        self.assertEqual(paid["order"]["pay_txn_no"], "TEST-WX-0001")
        self.assertEqual(self.reserved("M0000001"), before_reserved)
        self.assertEqual(self.stock("M0000001"), before_stock - _d("30"))
        self.assertEqual(self.sale_qty(order["order_id"], "M0000001"), _d("30"))
        # 实付 10 元 → 10 分
        self.assertEqual(self.points("C0000001"), before_points + 10)

    # ---- 余额支付：不计分 ---------------------------------------------
    def test_balance_payment_no_points(self):
        before_balance = self.balance("C0000001")
        before_points = self.points("C0000001")

        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000001",
        )
        order_id = detail["order"]["order_id"]
        self.assertEqual(self.balance("C0000001"), before_balance)

        paid = svc.pay_order(
            order_id,
            pay_method="BALANCE",
            role="customer",
            member_id="C0000001",
        )
        self.assertEqual(paid["order"]["order_status"], "PAID")
        self.assertEqual(self.balance("C0000001"), before_balance - _d("8.00"))
        self.assertEqual(self.points("C0000001"), before_points)

    # ---- 取消 PENDING：释放预占 + 退券 --------------------------------
    def test_cancel_pending_releases_stock_and_returns_coupon(self):
        coupon_id = self.coupon_id("C0000002")
        before_reserved = self.reserved("M0000001")

        detail = svc.create_order(
            items=[self.base_item("P0000001", 3)],
            role="customer",
            member_id="C0000002",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
        )
        order = detail["order"]
        self.assertEqual(order["order_status"], "PENDING")
        self.assertEqual(_d(order["coupon_discount"]), _d("5.00"))
        self.assertEqual(_d(order["total_amount"]), _d("31.00"))
        self.assertEqual(self.coupon_status(coupon_id), "LOCKED")
        self.assertEqual(self.reserved("M0000001"), before_reserved + _d("90"))

        cancelled = svc.cancel_order(
            order["order_id"], role="customer", member_id="C0000002"
        )
        self.assertEqual(cancelled["order"]["order_status"], "CANCELLED")
        self.assertEqual(self.reserved("M0000001"), before_reserved)
        # docs/01 §4.4：退回为“未使用”，不是作废（2026-10-28 交叉复核 P1-1）
        self.assertEqual(self.coupon_status(coupon_id), "UNUSED")
        self.assertIsNone(self.used_order_id(coupon_id))
        self.assertEqual(self.sale_qty(order["order_id"], "M0000001"), _d("0"))

    def test_returned_coupon_can_be_used_again(self):
        """P1-1 回归：取消后退回的券必须回到 UNUSED，且能再用在同一会员的新订单上。"""
        coupon_id = self.coupon_id("C0000002")
        first = svc.create_order(
            items=[self.base_item("P0000001", 3)],
            role="customer",
            member_id="C0000002",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
        )["order"]
        svc.cancel_order(first["order_id"], role="customer", member_id="C0000002")
        self.assertEqual(self.coupon_status(coupon_id), "UNUSED")
        self.assertIsNone(self.used_order_id(coupon_id))
        # 退回后重新出现在“可用券”列表里（页面下拉也用这个函数）
        usable = [c["member_coupon_id"] for c in svc.list_usable_coupons(
            "C0000002", "36.00", today=FIXED_DAY, role="customer")]
        self.assertIn(coupon_id, usable)

        second = svc.create_order(
            items=[self.base_item("P0000001", 3)],
            role="customer",
            member_id="C0000002",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
        )["order"]
        self.assertEqual(_d(second["coupon_discount"]), _d("5.00"))
        self.assertEqual(self.coupon_status(coupon_id), "LOCKED")
        self.assertEqual(self.used_order_id(coupon_id), second["order_id"])

    # ---- 配方在下单后被修改（已知限制的回归用例） ---------------------
    def test_cancel_after_recipe_changed_reports_business_rule(self):
        """P1-2 回归：改配方导致预占不够时，取消应报 E4001（业务规则）+ 可行动提示，
        而不是 E5003（“事务失败，请重试”——重试永远失败）；订单仍为 PENDING。"""
        order = svc.create_order(
            items=[self.base_item("P0000005", 1)],
            role="customer",
            member_id="C0000001",
        )["order"]
        self.cur.execute(
            "UPDATE dbo.item_recipe SET qty = 60 WHERE product_id = 'P0000005' "
            "AND material_id = 'M0000005'"
        )
        with self.assertRaises(AppError) as ctx:
            svc.cancel_order(order["order_id"], role="clerk")
        self.assertEqual(ctx.exception.code, "E4001")
        self.assertIn("配方", ctx.exception.message or "")
        self.assertEqual(self.order_status(order["order_id"]), "PENDING")
        self.assertEqual(self.reserved("M0000005"), _d("30.00"))

    def test_pay_after_recipe_changed_reports_business_rule(self):
        """P1-2 回归：改配方后支付不够预占，同样报 E4001 且提示配方可能已变更。"""
        order = svc.create_order(
            items=[self.base_item("P0000005", 1)],
            role="customer",
            member_id="C0000001",
        )["order"]
        self.cur.execute(
            "UPDATE dbo.item_recipe SET qty = 60 WHERE product_id = 'P0000005' "
            "AND material_id = 'M0000005'"
        )
        with self.assertRaises(AppError) as ctx:
            svc.pay_order(
                order["order_id"],
                pay_method="ONLINE",
                pay_txn_no="TEST-RECIPE-0001",
                role="customer",
                member_id="C0000001",
            )
        self.assertEqual(ctx.exception.code, "E4001")
        self.assertIn("配方", ctx.exception.message or "")
        self.assertEqual(self.order_status(order["order_id"]), "PENDING")
        self.assertEqual(self.sale_qty(order["order_id"], "M0000005"), _d("0"))

    # ---- 退款：线上单冲正积分 -----------------------------------------
    def test_refund_online_reverses_points(self):
        before_points = self.points("C0000001")
        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000001",
        )
        order_id = detail["order"]["order_id"]
        svc.pay_order(
            order_id,
            pay_method="ONLINE",
            pay_txn_no="TEST-WX-0002",
            role="customer",
            member_id="C0000001",
        )
        self.assertEqual(self.points("C0000001"), before_points + 8)
        stock_after_pay = self.stock("M0000003")

        refunded = svc.refund_order(order_id, role="manager")
        self.assertEqual(refunded["order"]["order_status"], "REFUNDED")
        self.assertEqual(_d(refunded["order"]["refund_amount"]), _d("8.00"))
        self.assertEqual(self.points("C0000001"), before_points)
        # 已耗用原料不追回：退款不恢复库存
        self.assertEqual(self.stock("M0000003"), stock_after_pay)

    # ---- 退款：退券 + 冲正积分 ---------------------------------------
    def test_refund_returns_coupon_and_reverses_points(self):
        coupon_id = self.coupon_id("C0000002")
        before_points = self.points("C0000002")

        detail = svc.create_order(
            items=[self.base_item("P0000001", 3)],
            role="customer",
            member_id="C0000002",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
        )
        order_id = detail["order"]["order_id"]
        svc.pay_order(
            order_id,
            pay_method="ONLINE",
            pay_txn_no="TEST-WX-0006",
            role="customer",
            member_id="C0000002",
        )
        self.assertEqual(self.coupon_status(coupon_id), "USED")
        self.assertEqual(self.points("C0000002"), before_points + 31)

        refunded = svc.refund_order(order_id, role="manager")
        self.assertEqual(refunded["order"]["order_status"], "REFUNDED")
        # 退款退券回“未使用”（docs/01 §4.4），且解除与退款单的关联
        self.assertEqual(self.coupon_status(coupon_id), "UNUSED")
        self.assertIsNone(self.used_order_id(coupon_id))
        self.assertEqual(self.points("C0000002"), before_points)

    # ---- 退款：余额单退回余额 -----------------------------------------
    def test_refund_balance_returns_balance(self):
        before_balance = self.balance("C0000001")
        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000001",
        )
        order_id = detail["order"]["order_id"]
        svc.pay_order(order_id, pay_method="BALANCE", role="customer", member_id="C0000001")
        self.assertEqual(self.balance("C0000001"), before_balance - _d("8.00"))

        svc.refund_order(order_id, role="manager")
        self.assertEqual(self.balance("C0000001"), before_balance)

    # ---- 制作状态流转 -------------------------------------------------
    def test_update_status_paid_making_completed(self):
        detail = svc.create_order(
            items=[self.base_item("P0000001", 1)],
            role="clerk",
            employee_id="E0000001",
            pay_method="CASH",
        )
        order_id = detail["order"]["order_id"]
        svc.update_order_status(order_id, to_status="MAKING", role="maker")
        self.assertEqual(self.order_status(order_id), "MAKING")
        svc.update_order_status(order_id, to_status="COMPLETED", role="maker")
        self.assertEqual(self.order_status(order_id), "COMPLETED")
        self.assertIsNotNone(
            self.scalar("SELECT completed_at FROM dbo.shop_order WHERE order_id = ?", order_id)
        )

    def test_update_status_invalid_pending_to_completed(self):
        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000001",
        )
        self.assert_app_error(
            "E4001",
            svc.update_order_status,
            detail["order"]["order_id"],
            to_status="COMPLETED",
            role="maker",
        )

    # ---- 平台单：直接 PAID、不预占 ------------------------------------
    def test_platform_order_paid_without_reservation(self):
        before_stock = self.stock("M0000001")
        before_reserved = self.reserved("M0000001")

        detail = svc.create_order(
            items=[self.base_item("P0000001", 1)],
            role="clerk",
            employee_id="E0000001",
            channel="PLATFORM",
            platform={
                "platform_order_no": "TEST-MT-0001",
                "platform_name": "MEITUAN",
                "delivery_address": "测试地址 1 号",
                "delivery_fee": 3,
                "commission_rate": "0.2000",
            },
        )
        order = detail["order"]
        self.assertEqual(order["order_status"], "PAID")
        self.assertIsNone(order["order_mode"])
        self.assertIsNone(order["pay_method"])
        self.assertIsNone(order["member_id"])
        self.assertIsNotNone(detail["platform"])
        self.assertEqual(_d(detail["platform"]["commission_amount"]), _d("2.40"))
        self.assertEqual(self.stock("M0000001"), before_stock - _d("30"))
        self.assertEqual(self.reserved("M0000001"), before_reserved)
        self.assertEqual(self.sale_qty(order["order_id"], "M0000001"), _d("30"))

    # ---- 支付幂等 / 重复流水 ------------------------------------------
    def test_pay_is_idempotent_with_same_txn(self):
        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000001",
        )
        order_id = detail["order"]["order_id"]
        svc.pay_order(order_id, pay_method="ONLINE", pay_txn_no="TEST-WX-0003", role="customer", member_id="C0000001")
        stock_after = self.stock("M0000003")
        points_after = self.points("C0000001")

        again = svc.pay_order(
            order_id, pay_method="ONLINE", pay_txn_no="TEST-WX-0003", role="customer", member_id="C0000001"
        )
        self.assertEqual(again["order"]["order_status"], "PAID")
        self.assertEqual(self.stock("M0000003"), stock_after)
        self.assertEqual(self.points("C0000001"), points_after)

    def test_pay_txn_conflict_with_other_order(self):
        first = svc.create_order(items=[self.base_item("P0000003", 1)], role="customer", member_id="C0000001")
        second = svc.create_order(items=[self.base_item("P0000003", 1)], role="customer", member_id="C0000001")
        svc.pay_order(first["order"]["order_id"], pay_method="ONLINE", pay_txn_no="TEST-WX-0004", role="customer", member_id="C0000001")
        self.assert_app_error(
            "E4001",
            svc.pay_order,
            second["order"]["order_id"],
            pay_method="ONLINE",
            pay_txn_no="TEST-WX-0004",
            role="customer",
            member_id="C0000001",
        )

    # ---- 业务校验 -----------------------------------------------------
    def test_addon_must_be_addon_kind(self):
        self.assert_app_error(
            "E4001",
            svc.create_order,
            items=[self.base_item("P0000001", 1, [{"material_id": "M0000002", "qty": 1}])],
            role="clerk",
            employee_id="E0000001",
            pay_method="CASH",
        )

    def test_insufficient_stock_leaves_no_reservation(self):
        before_reserved = self.reserved("M0000001")
        self.assert_app_error(
            "E4001",
            svc.create_order,
            items=[self.base_item("P0000001", 99, [{"material_id": "M0000001", "qty": 99}])],
            role="clerk",
            employee_id="E0000001",
            pay_method="CASH",
        )
        self.assertEqual(self.reserved("M0000001"), before_reserved)

    def test_invalid_qty(self):
        self.assert_app_error(
            "E1001",
            svc.create_order,
            items=[{"product_id": "P0000003", "qty": 0}],
            role="clerk",
            employee_id="E0000001",
            pay_method="CASH",
        )

    def test_balance_insufficient(self):
        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000002",
        )
        self.assert_app_error(
            "E4001",
            svc.pay_order,
            detail["order"]["order_id"],
            pay_method="BALANCE",
            role="customer",
            member_id="C0000002",
        )
        self.assertEqual(self.order_status(detail["order"]["order_id"]), "PENDING")

    def test_coupon_threshold_not_met(self):
        coupon_id = self.coupon_id("C0000002")
        self.assert_app_error(
            "E4001",
            svc.create_order,
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000002",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
        )
        self.assertEqual(self.coupon_status(coupon_id), "UNUSED")

    def test_coupon_not_owned(self):
        coupon_id = self.coupon_id("C0000002")
        self.assert_app_error(
            "E4001",
            svc.create_order,
            items=[self.base_item("P0000001", 3)],
            role="customer",
            member_id="C0000001",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
        )

    # ---- 权限边界：店长不能下单（矩阵 / 服务层 / 页面三层一致） ---------
    def test_manager_cannot_create_order(self):
        """P2-3 回归：``docs/01`` 把“收银台代录”归给收银员，店长职责是维护与退款，
        因此 ``create_order(role="manager")`` 必须 403，而不是静默成功。"""
        self.assert_app_error(
            "E2003",
            svc.create_order,
            items=[self.base_item("P0000003", 1)],
            role="manager",
            employee_id="E0000004",
            pay_method="CASH",
        )
        # 平台单同样要过这道闸（平台单入口是收银员，不是店长）
        self.assert_app_error(
            "E2003",
            svc.create_order,
            items=[self.base_item("P0000002", 1)],
            role="manager",
            employee_id="E0000004",
            channel="PLATFORM",
            platform={
                "platform_order_no": "MT-MGR-0001",
                "platform_name": "MEITUAN",
                "delivery_address": "不应落库",
                "delivery_fee": 0,
                "commission_rate": "0.2",
            },
        )

    def test_customer_cannot_platform(self):
        self.assert_app_error(
            "E2003",
            svc.create_order,
            items=[self.base_item("P0000001", 1)],
            role="customer",
            member_id="C0000001",
            channel="PLATFORM",
            platform={
                "platform_order_no": "TEST-X",
                "platform_name": "MEITUAN",
                "delivery_address": "x",
                "delivery_fee": 0,
            },
        )

    # ---- 查询：行级隔离 -----------------------------------------------
    def test_customer_detail_row_isolation(self):
        own = svc.get_order_detail("202609160002", member_id="C0000001", role="customer")
        self.assertEqual(own["order"]["order_id"], "202609160002")
        self.assert_app_error(
            "E3001",
            svc.get_order_detail,
            "202609160005",
            member_id="C0000001",
            role="customer",
        )

    def test_customer_cannot_pay_other_member_order(self):
        detail = svc.create_order(
            items=[self.base_item("P0000003", 1)],
            role="customer",
            member_id="C0000001",
        )
        self.assert_app_error(
            "E3001",
            svc.pay_order,
            detail["order"]["order_id"],
            pay_method="ONLINE",
            pay_txn_no="TEST-WX-0005",
            role="customer",
            member_id="C0000002",
        )

    def test_cash_member_coupon_used_and_points_earned(self):
        coupon_id = self.coupon_id("C0000002")
        before_points = self.points("C0000002")

        detail = svc.create_order(
            items=[self.base_item("P0000001", 3)],
            role="clerk",
            employee_id="E0000001",
            member_id="C0000002",
            member_coupon_id=coupon_id,
            today=FIXED_DAY,
            pay_method="CASH",
        )
        order = detail["order"]
        self.assertEqual(order["order_status"], "PAID")
        self.assertEqual(_d(order["coupon_discount"]), _d("5.00"))
        self.assertEqual(_d(order["total_amount"]), _d("31.00"))
        self.assertEqual(self.coupon_status(coupon_id), "USED")
        self.assertEqual(self.points("C0000002"), before_points + 31)

    def test_platform_zero_commission_rate(self):
        detail = svc.create_order(
            items=[self.base_item("P0000001", 1)],
            role="clerk",
            employee_id="E0000001",
            channel="PLATFORM",
            platform={
                "platform_order_no": "TEST-MT-0002",
                "platform_name": "MEITUAN",
                "delivery_address": "测试地址 2 号",
                "delivery_fee": 0,
                "commission_rate": 0,
            },
        )
        self.assertEqual(_d(detail["platform"]["commission_rate"]), _d("0.0000"))
        self.assertEqual(_d(detail["platform"]["commission_amount"]), _d("0.00"))

    def test_lists_and_detail_scopes(self):
        own = svc.list_orders(role="customer", member_id="C0000001")
        self.assertTrue(own)
        self.assertTrue(all(o["member_id"] == "C0000001" for o in own))
        self.assertEqual(
            [o["order_id"] for o in own],
            [o["order_id"] for o in svc.list_my_orders(member_id="C0000001", role="customer")],
        )
        detail = svc.get_order_detail("202609160002", role="manager", include_stock_logs=True)
        self.assertEqual(detail["order"]["order_id"], "202609160002")
        self.assertIn("stock_logs", detail)

    # ---- 多行订单 -----------------------------------------------------
    def test_multi_line_order_ids_and_amounts(self):
        detail = svc.create_order(
            items=[
                self.base_item("P0000001", 2),
                self.base_item("P0000003", 1),
            ],
            role="clerk",
            employee_id="E0000001",
            pay_method="CASH",
        )
        order = detail["order"]
        self.assertEqual(_d(order["item_subtotal"]), _d("32.00"))
        self.assertEqual(_d(order["total_amount"]), _d("32.00"))
        self.assertEqual([int(i["item_line"]) for i in detail["items"]], [1, 2])
        self.assertEqual(_d(detail["items"][0]["unit_price"]), _d("12.00"))
        self.assertEqual(_d(detail["items"][1]["unit_price"]), _d("8.00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
