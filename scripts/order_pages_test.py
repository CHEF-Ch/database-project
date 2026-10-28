"""⑤ 订单页面（Chen）自检：路由权限、页面渲染、下单/支付/取消/退款/制作状态。

做法与 ``scripts/order_service_test.py`` 一致：**整轮跑在一个外层事务里，最后整体
回滚**，所以不会污染 ``MilkToaShop_v01`` 的 seed 数据。例外是要用真实 HTTP 会话，
因此把 ``db_cursor`` 换成“同一个连接 + SAVEPOINT”的假上下文（每个请求一个
savepoint），页面层与业务层看到的是同一个事务。

覆盖（对应 docs/09-应用开发/测试用例.md 的 A/B/E/F/G 组）：
  · 未登录 401；五个角色的读写权限正反例；水平越权（IDOR）；
  · 列表筛选/日期/非法参数 400/404/405；
  · 顾客自助下单（PENDING）、店员现金单（PAID）、平台单（PAID + order_platform）；
  · 支付确认、取消（释放预占）、店长整单退款（不回补库存）、制作员状态机；
  · 错误响应不泄露 SQL/表名/连接信息。

用法（仓库根目录）：
    python scripts/order_pages_test.py            # 只跑并打印
    python scripts/order_pages_test.py --write    # 另写 result/第7周-⑤订单页面自检-<日期>.txt

退出码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                    # noqa: E402
from app.config import Config                                 # noqa: E402
from app.dao.connection import connect                        # noqa: E402
import app.services.inventory_service as inventory_svc        # noqa: E402
import app.services.order_service as order_svc                # noqa: E402
import app.services.product_service as product_svc            # noqa: E402

PASSWORD = {
    "clerk1": "clerk123",
    "customer1": "cust123",
    "maker1": "maker123",
    "stock1": "stock123",
    "manager1": "mgr123",
}

# --sql-auth 模式下用 sql/app_login.sql 里的示例密码（可由同名环境变量覆盖）
_SQL_LOGIN_PWD = {
    "DB_PWD_LOGIN_CLERK": "Clerk#2026",
    "DB_PWD_LOGIN_CUSTOMER": "Cust#2026",
    "DB_PWD_LOGIN_MAKER": "Maker#2026",
    "DB_PWD_LOGIN_STOCK": "Stock#2026",
    "DB_PWD_LOGIN_MANAGER": "Mgr#2026",
    "DB_PWD_LOGIN_APP": "App#2026",
}

# 泄露自检：错误页/正常页都不该出现的实现细节
_LEAK_TOKENS = ("SELECT ", "INSERT INTO", "dbo.", "pyodbc", "SQLEXPRESS", "Traceback", "ODBC Driver")


def _d(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


class Harness:
    """一个 outer transaction + 假 db_cursor + Flask 测试客户端。"""

    def __init__(self) -> None:
        # 外层事务连接：trusted 用当前 Windows 账户；sql 模式借用 role_app（能读写本用例涉及的表）
        self.conn = connect("app" if Config.DB_AUTH == "sql" else None)
        self.cur = self.conn.cursor()
        self.cur.execute("SET XACT_ABORT OFF")     # 单条语句报错不 doom 外层事务
        self.cur.execute("SAVE TRANSACTION sp_outer")

        # 三处都替换：订单/商品必用；库存服务当前不在这条链上，但一并换掉，
        # 避免以后 order_service 改为委托 inventory_service 时测试静默真提交、污染 seed
        self._orig = (order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor)
        harness = self

        @contextmanager
        def fake_db_cursor(role=None, commit=False):
            harness.cur.execute("SAVE TRANSACTION sp_call")
            try:
                yield harness.cur
            except BaseException:
                harness.cur.execute("ROLLBACK TRANSACTION sp_call")
                raise
            else:
                if not commit:
                    harness.cur.execute("ROLLBACK TRANSACTION sp_call")

        order_svc.db_cursor = fake_db_cursor
        product_svc.db_cursor = fake_db_cursor
        inventory_svc.db_cursor = fake_db_cursor

        self.app = create_app()
        self.app.config["SERVER_NAME"] = "localhost"
        self.client = self.app.test_client()
        self.rows: list[tuple[str, int, int, str, str]] = []
        self.created: list[str] = []

    # ---- 断言收集 ------------------------------------------------------
    def check(self, label: str, expect: int, actual: int, note: str = "") -> None:
        self.rows.append((label, expect, actual, "PASS" if expect == actual else "FAIL", note))

    def check_true(self, label: str, cond: bool, note: str = "") -> None:
        self.rows.append((label, 1, 1 if cond else 0, "PASS" if cond else "FAIL", note))

    # ---- 请求 ----------------------------------------------------------
    def login(self, username: str) -> None:
        resp = self.client.post(
            "/login", data={"username": username, "password": PASSWORD[username]}
        )
        assert resp.status_code == 302, (username, resp.status_code)

    def logout(self) -> None:
        self.client.get("/logout")

    def get(self, path: str, expect: int, note: str = "") -> str:
        resp = self.client.get(path)
        body = resp.get_data(as_text=True)
        self.check(f"GET {path}", expect, resp.status_code, note)
        return body

    def post(self, path: str, data: dict, expect: int, note: str = "") -> str:
        resp = self.client.post(path, data=data)
        body = resp.get_data(as_text=True)
        self.check(f"POST {path}", expect, resp.status_code, note)
        if expect == 302 and resp.status_code == 302:
            location = resp.headers.get("Location", "")
            if "/orders/" in location and "new" not in location:
                self.created.append(location.rsplit("/", 1)[-1])
        return body

    # ---- 数据库小工具 --------------------------------------------------
    def one(self, sql: str, *params):
        self.cur.execute(sql, *params)
        row = self.cur.fetchone()
        return row[0] if row else None

    def order_row(self, order_id: str):
        self.cur.execute(
            "SELECT channel, order_status, member_id, employee_id, item_subtotal, "
            "coupon_discount, total_amount, pay_method, completed_at, refund_amount "
            "FROM dbo.shop_order WHERE order_id = ?",
            order_id,
        )
        return self.cur.fetchone()

    def stock(self, material_id: str) -> Decimal:
        return _d(self.one("SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", material_id))

    def reserved(self, material_id: str) -> Decimal:
        return _d(self.one("SELECT reserved_qty FROM dbo.item_material WHERE material_id = ?", material_id))

    def order_count(self) -> int:
        return int(self.one("SELECT COUNT(*) FROM dbo.shop_order"))

    def last_order_id(self) -> str | None:
        return self.one("SELECT MAX(order_id) FROM dbo.shop_order")

    def max_stock_log_id(self) -> int:
        return int(self.one("SELECT ISNULL(MAX(stock_log_id), 0) FROM dbo.inv_stock_log"))

    def reserved_total(self) -> Decimal:
        """全库预占合计：取消/支付前后用它看“预占是否真的被释放”。"""
        return _d(self.one("SELECT SUM(reserved_qty) FROM dbo.item_material"))

    def usable_coupons(self, member_id: str) -> int:
        """按“今天”算该会员可用的券数，避免用例随券有效期失效。"""
        self.cur.execute(
            """
            SELECT COUNT(*)
            FROM dbo.mkt_member_coupon AS mc
            JOIN dbo.mkt_coupon_rule AS r ON r.coupon_rule_id = mc.coupon_rule_id
            WHERE mc.member_id = ? AND mc.coupon_status = 'UNUSED'
              AND r.valid_from <= CAST(SYSDATETIME() AS date)
              AND r.valid_to   >= CAST(SYSDATETIME() AS date)
            """,
            member_id,
        )
        return int(self.cur.fetchone()[0])

    # ---- 收尾 ----------------------------------------------------------
    def close(self) -> None:
        order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor = self._orig
        try:
            self.conn.rollback()          # 整轮回滚：seed 数据与 IDENTITY 之外的一切还原
        finally:
            self.cur.close()
            self.conn.close()


def _leaks(body: str) -> bool:
    return any(token in body for token in _LEAK_TOKENS)


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def run_read_cases(h: Harness) -> None:
    """A/B/F/G 组：未登录、五角色权限正反例、筛选、越权、详情按钮显隐。"""
    body = h.get("/orders/", 401, "未登录")
    h.check_true("未登录错误页不含实现细节", not _leaks(body))

    h.login("clerk1")
    body = h.get("/orders/", 200, "店员可看全量")
    h.check_true("列表含基线订单 202609160002", "202609160002" in body)
    body = h.get("/orders/?status=PENDING", 200, "按状态筛选")
    h.check_true("筛选结果含待支付单 202609160005", "202609160005" in body)
    h.check_true("筛选结果不含已完成单 202609160002", "202609160002" not in body)
    h.get("/orders/?status=BOGUS", 400, "非法状态 E1001")
    h.get("/orders/?date_from=2026-13-01", 400, "非法日期 E1001")
    h.get("/orders/?limit=abc", 400, "非法分页 E1001")
    page1 = h.get("/orders/?limit=2", 200, "分页正例：每页 2 条")
    h.check_true("分页只渲染 limit 行", page1.count('href="/orders/2026') == 2, str(page1.count('href="/orders/2026')))
    h.check_true("分页出现下一页链接（offset=2）", "offset=2" in page1)
    page2 = h.get("/orders/?limit=2&offset=2", 200, "分页第二页")
    h.check_true("两页内容不同（偏移生效）", "202609160007" in page1 and "202609160007" not in page2)
    empty = h.get("/orders/?q=NOPE-NOT-EXIST", 200, "列表空态")
    h.check_true("空结果不显示“第 1—0 条”", "第 1—0 条" not in empty and "共 0 条" in empty)
    body = h.get("/orders/new", 200, "店员可下单")
    h.check_true("下单页渲染商品行 qty.P0000001", 'name="qty.P0000001"' in body)
    h.check_true("下单页渲染加料行", 'name="addon.P0000001.M0000004"' in body)
    body = h.get("/orders/new?member_id=C0000002", 200, "店员代客选会员")
    # 与库内“该会员此刻是否有可用券”交叉核对：券过期后会自动变成“不出现”，不会假通过
    usable = h.usable_coupons("C0000002")
    h.check_true(
        "券下拉与库内可用券一致（有则出现，无则不出现）",
        ("满30减5" in body) == (usable > 0),
        f"库内可用券 {usable} 张",
    )
    h.check_true(
        "券选项带门槛/优惠数据属性（有券时）",
        usable == 0 or ('data-threshold="30.00"' in body and 'data-discount="5.00"' in body),
    )
    h.check_true("下单页有“按会员号列券”的 GET 入口", "查询该会员的可用券" in body)
    body = h.get("/orders/202609160002", 200, "店员看详情")
    h.check_true("店员详情不显示整单退款按钮", "整单退款" not in body)
    h.check_true("店员详情不显示开始制作按钮", "开始制作" not in body)
    h.get("/orders/P9999999", 404, "订单不存在 E3001")
    body = h.get("/orders/mine", 200, "店员查“我的订单”")
    h.check_true("店员无会员号时给出空态提示", "没有绑定会员号" in body)
    h.logout()

    h.login("maker1")
    h.get("/orders/", 200, "制作员可看订单队列")
    h.get("/orders/new", 403, "制作员不能下单")
    body = h.get("/orders/202609160001", 200, "制作员看已完成单")
    h.check_true("已完成单无状态按钮", "开始制作" not in body and "完成制作" not in body)
    h.logout()

    h.login("stock1")
    h.get("/orders/", 403, "库存管理员无订单读权限")
    h.get("/orders/new", 403, "库存管理员不能下单")
    h.get("/orders/202609160002", 403, "库存管理员既无 order.read 也无 order.read_own → 详情 403")
    h.logout()

    h.login("manager1")
    h.get("/orders/", 200, "店长可看订单")
    h.get("/orders/new", 403, "店长无 order.create（权限矩阵）")
    body = h.get("/orders/202609160004", 200, "店长看平台单详情")
    h.check_true("平台单详情含平台信息", "平台信息" in body and "美团" in body)
    body = h.get("/orders/202609160006", 200, "店长看已退款单")
    refund_amount = h.one("SELECT refund_amount FROM dbo.shop_order WHERE order_id = '202609160006'")
    h.check_true(
        "已退款单展示退款额（金额本身，不只是状态徽标）",
        f"已退 <strong>{_d(refund_amount)}</strong>" in body,
        str(refund_amount),
    )
    h.logout()

    h.login("customer1")
    h.get("/orders/", 403, "顾客不能看全量订单")
    body = h.get("/orders/mine", 200, "顾客看我的订单")
    h.check_true("我的订单含 C0000001 的单", "202609160002" in body)
    h.check_true("我的订单不含 C0000002 的单", "202609160005" not in body)
    h.get("/orders/202609160002", 200, "顾客看自己的单")
    body = h.get("/orders/202609160005", 404, "顾客看别人的单 → 404（IDOR）")
    h.check_true("越权详情页不含他人订单号", "202609160005" not in body)
    body = h.get("/orders/new", 200, "顾客可自助点单")
    h.check_true("顾客下单页不出现平台单字段", "platform_order_no" not in body)
    h.check_true("顾客下单页会员号取自 session", "C0000001" in body)
    h.logout()


def run_write_cases(h: Harness) -> None:
    """E 组：现金单 / 自助待支付 / 平台单 / 支付 / 取消 / 退款 / 制作状态。"""
    base_orders = h.order_count()

    # ---- 1) 店员现金单：1 杯 + 1 份加料，应当场 PAID 并扣料写流水 ----
    h.login("clerk1")
    stock_before = h.stock("M0000001")
    log_before = h.max_stock_log_id()
    body = h.post(
        "/orders/",
        {
            "channel": "COUNTER",
            "order_mode": "COUNTER_CASHIER",
            "pay_method": "CASH",
            "qty.P0000001": "1",
            "sugar.P0000001": "HALF",
            "ice.P0000001": "LESS",
            "temp.P0000001": "HOT",
            "addon.P0000001.M0000004": "1",
        },
        302,
        "店员现金单",
    )
    cash_order = h.last_order_id()
    h.check_true("现金单已落库", cash_order is not None, cash_order or "")
    detail_body = h.get(f"/orders/{cash_order}", 200, "下单后的重定向目标可访问")
    h.check_true("详情页展示新单号", cash_order in detail_body)
    cash = h.order_row(cash_order)
    h.check_true("现金单状态 PAID", cash[1] == "PAID", str(cash[1]))
    h.check_true("现金单金额 = 12.00 + 1.00 加料", _d(cash[6]) == _d("13.00"), str(cash[6]))
    h.check_true("现金单扣现存 M0000001 -30", h.stock("M0000001") == stock_before - 30, str(h.stock("M0000001")))
    h.check_true("现金单写 SALE 流水", h.max_stock_log_id() > log_before)
    h.check_true(
        "现金单明细糖/冰/温快照",
        h.one(
            "SELECT CONCAT(sugar_level, '/', ice_level, '/', temp_level) FROM dbo.order_item "
            "WHERE order_id = ? AND item_line = 1",
            cash_order,
        )
        == "HALF/LESS/HOT",
    )
    h.check_true("现金单加料价快照 1.00", _d(h.one(
        "SELECT addon_price FROM dbo.order_item_addon WHERE order_id = ?", cash_order)) == _d("1.00"))

    # ---- 2) 顾客自助单：PENDING + 预占；表单伪造 member_id 不生效 ----
    h.logout()
    h.login("customer1")
    reserved_before = h.reserved("M0000001")
    stock_before = h.stock("M0000001")
    h.post(
        "/orders/",
        {
            "channel": "COUNTER",
            "order_mode": "COUNTER_SELF",
            "member_id": "C0000002",          # 伪造：必须被忽略（ORD-09）
            "pay_method": "CASH",             # 伪造：顾客不能现金当场结清
            "qty.P0000001": "1",
        },
        302,
        "顾客自助下单",
    )
    self_order = h.last_order_id()
    self_row = h.order_row(self_order)
    h.check_true("自助单状态 PENDING", self_row[1] == "PENDING", str(self_row[1]))
    h.check_true("会员号只取 session（C0000001）", self_row[2] == "C0000001", str(self_row[2]))
    h.check_true("自助单只预占不改现存", h.stock("M0000001") == stock_before, str(h.stock("M0000001")))
    h.check_true(
        "自助单预占 +30",
        h.reserved("M0000001") == reserved_before + 30,
        str(h.reserved("M0000001")),
    )

    # ---- 3) 顾客不能伪造平台单 ----
    before = h.order_count()
    h.post(
        "/orders/",
        {
            "channel": "PLATFORM",
            "platform_order_no": "MT-FAKE-001",
            "platform_name": "MEITUAN",
            "delivery_address": "伪造地址",
            "qty.P0000001": "1",
        },
        403,
        "顾客伪造平台单 → 403",
    )
    h.check_true("伪造平台单未落库", h.order_count() == before)

    # ---- 4) 顾客支付自己的 PENDING 单（线上流水号必填） ----
    points_before_pay = int(h.one("SELECT points FROM dbo.member_member WHERE member_id = 'C0000001'"))
    h.post(f"/orders/{self_order}/pay", {"pay_method": "ONLINE"}, 400, "线上支付缺流水号 E1001")
    h.post(
        f"/orders/{self_order}/pay",
        {"pay_method": "ONLINE", "pay_txn_no": "ON-TEST-0001"},
        302,
        "顾客支付自己的单",
    )
    paid = h.order_row(self_order)
    h.check_true("支付后状态 PAID", paid[1] == "PAID", str(paid[1]))
    h.check_true("支付后预占归零", h.reserved("M0000001") == reserved_before, str(h.reserved("M0000001")))
    h.check_true("支付后现存 -30", h.stock("M0000001") == stock_before - 30, str(h.stock("M0000001")))
    points_after = int(h.one("SELECT points FROM dbo.member_member WHERE member_id = 'C0000001'"))
    # 断言“差值”，不能写 >= 12：seed 里 C0000001 本来就有 118 分，恒真断言等于没测
    h.check_true(
        "支付后会员积分 +12（满 1 元 1 分）",
        points_after - points_before_pay == 12,
        f"{points_before_pay} → {points_after}",
    )

    # ---- 5) 顾客不能退款、不能支付/取消别人的单 ----
    h.post(f"/orders/{self_order}/refund", {}, 403, "顾客无 order.refund")
    h.post(
        "/orders/202609160005/pay",
        {"pay_method": "ONLINE", "pay_txn_no": "ON-IDOR-0001"},
        404,
        "顾客支付他人 PENDING 单 → 404（不泄露是否存在）",
    )
    h.check_true("他人单未被支付", h.order_row("202609160005")[1] == "PENDING")
    h.post("/orders/202609160005/cancel", {}, 404, "顾客取消他人单 → 404")
    h.check_true(
        "他人 PENDING 单未被改动",
        h.order_row("202609160005")[1] == "PENDING",
    )
    h.logout()

    # ---- 6) 参数与业务规则失败不留半条数据 ----
    h.login("clerk1")
    before = h.order_count()
    h.post("/orders/", {"channel": "COUNTER", "qty.P0000001": "0"}, 400, "未选任何饮品 E1001")
    rejected = h.post(
        "/orders/",
        {"channel": "COUNTER", "pay_method": "CASH", "qty.P0000001": "abc"},
        400,
        "杯数非整数 E1001",
    )
    h.check_true("非法杯数也回填（value=\"abc\"）", 'value="abc"' in rejected and "下单未完成" in rejected)
    h.check_true("失败请求未产生订单", h.order_count() == before)

    # 表单里塞入“不属于该商品的加料”：页面层只按服务端商品 + 该商品可选加料收集，
    # 这个字段被丢弃（不报错），因此订单能成但不会落加料行；业务层 P9 校验见 ④ 用例。
    h.post(
        "/orders/",
        {"channel": "COUNTER", "pay_method": "CASH", "qty.P0000001": "1",
         "addon.P0000001.M0000001": "1"},
        302,
        "非本商品加料字段被页面层丢弃",
    )
    injected = h.last_order_id()
    h.check_true(
        "被丢弃的加料不落 order_item_addon",
        int(h.one("SELECT COUNT(*) FROM dbo.order_item_addon WHERE order_id = ?", injected)) == 0,
    )
    h.check_true(
        "被丢弃的加料不影响金额",
        _d(h.one("SELECT total_amount FROM dbo.shop_order WHERE order_id = ?", injected)) == _d("12.00"),
    )

    # ---- 7) 库存不足：把 M0000003 的可售量压到 0，下单应 409（并回填表单） ----
    reserved_m3 = h.reserved("M0000003")
    stock_m3 = h.stock("M0000003")
    h.cur.execute(
        "UPDATE dbo.item_material SET stock_qty = reserved_qty WHERE material_id = 'M0000003'"
    )
    try:
        before = h.order_count()
        body = h.post(
            "/orders/",
            {
                "channel": "COUNTER",
                "order_mode": "COUNTER_SELF",
                "pay_method": "ONLINE",
                "member_id": "C0000001",
                "qty.P0000001": "2",
                "sugar.P0000001": "HALF",
                "ice.P0000001": "LESS",
                "temp.P0000001": "HOT",
                "addon.P0000001.M0000004": "1",
            },
            409,
            "库存不足 E4001（回填表单）",
        )
        h.check_true("库存不足未产生订单", h.order_count() == before)
        h.check_true("库存不足未改动现存", h.stock("M0000003") == reserved_m3, str(h.stock("M0000003")))
        # 方案 A：表单错误在页面层就地回填重渲染（WTForms/Rails 做法），不丢用户输入
        h.check_true("失败页有回填提示条", "下单未完成" in body and "E4001" in body)
        h.check_true("回填杯数 2", 'name="qty.P0000001"' in body and 'value="2"' in body)
        h.check_true("回填糖度 HALF", 'value="HALF" selected' in body)
        h.check_true("回填冰量/温度", 'value="LESS" selected' in body and 'value="HOT" selected' in body)
        addon_input = re.search(
            r'name="addon\.P0000001\.M0000004"[^>]*value="([^"]*)"', body)
        h.check_true(
            "回填加料份数 1",
            addon_input is not None and addon_input.group(1) == "1",
            addon_input.group(1) if addon_input else "未匹配",
        )
        h.check_true("回填会员号", 'value="C0000001"' in body)
        h.check_true("回填下单方式", 'value="COUNTER_SELF" selected' in body)
    finally:
        h.cur.execute(
            "UPDATE dbo.item_material SET stock_qty = ? WHERE material_id = 'M0000003'", stock_m3
        )

    # ---- 8) 平台单：不预占、不积分、写 order_platform ----
    h.post(
        "/orders/",
        {
            "channel": "PLATFORM",
            "platform_order_no": "MT-TEST-0001",
            "platform_name": "MEITUAN",
            "delivery_address": "中山大学东校园 1 栋",
            "delivery_fee": "3.00",
            "commission_rate": "0.2000",
            "qty.P0000002": "1",
        },
        302,
        "店员录入平台单",
    )
    platform_order = h.last_order_id()
    prow = h.order_row(platform_order)
    h.check_true("平台单状态 PAID", prow[1] == "PAID", str(prow[1]))
    h.check_true("平台单无会员", prow[2] is None, str(prow[2]))
    h.check_true(
        "平台单写 1 行 order_platform",
        int(h.one("SELECT COUNT(*) FROM dbo.order_platform WHERE order_id = ?", platform_order)) == 1,
    )
    h.check_true(
        "平台单抽成 = 15.00 × 20% = 3.00",
        _d(h.one("SELECT commission_amount FROM dbo.order_platform WHERE order_id = ?", platform_order))
        == _d("3.00"),
    )

    # ---- 9) 制作状态机（制作员） ----
    h.logout()
    h.login("maker1")
    h.post(f"/orders/{cash_order}/status", {"to_status": "COMPLETED"}, 409, "PAID→COMPLETED 非法")
    h.post(f"/orders/{cash_order}/status", {"to_status": "MAKING"}, 302, "PAID→MAKING")
    h.check_true("状态变为 MAKING", h.order_row(cash_order)[1] == "MAKING")
    h.post(f"/orders/{cash_order}/status", {"to_status": "COMPLETED"}, 302, "MAKING→COMPLETED")
    done = h.order_row(cash_order)
    h.check_true("状态变为 COMPLETED", done[1] == "COMPLETED")
    h.check_true("completed_at 已写入", done[8] is not None)
    h.logout()

    # ---- 10) 退款：店员 403、店长可整单退（库存不回补） ----
    h.login("clerk1")
    h.post(f"/orders/{cash_order}/refund", {}, 403, "店员无 order.refund")
    h.logout()
    h.login("maker1")
    h.post(f"/orders/{cash_order}/refund", {}, 403, "制作员无 order.refund（PERM-11）")
    h.logout()
    h.login("clerk1")
    h.post(f"/orders/{cash_order}/cancel", {}, 409, "PAID 单不能取消（ORD-23，只能走退款）")
    h.check_true("PAID 单状态未被取消", h.order_row(cash_order)[1] in {"PAID", "MAKING", "COMPLETED"})
    h.logout()
    h.login("manager1")
    stock_before_refund = h.stock("M0000001")
    log_before_refund = h.max_stock_log_id()
    h.post(f"/orders/{cash_order}/refund", {}, 302, "店长整单退款")
    refunded = h.order_row(cash_order)
    h.check_true("状态变为 REFUNDED", refunded[1] == "REFUNDED", str(refunded[1]))
    h.check_true("退款额 = 应付 13.00", _d(refunded[9]) == _d("13.00"), str(refunded[9]))
    h.check_true("退款不回补库存", h.stock("M0000001") == stock_before_refund)
    h.check_true("退款不新增库存流水（ORD-24）", h.max_stock_log_id() == log_before_refund)
    h.post(f"/orders/{cash_order}/refund", {}, 409, "重复退款 E4001")
    h.logout()

    # ---- 11) 取消：店员取消他人 PENDING 单，释放预占 ----
    h.login("clerk1")
    reserved_before_cancel = h.reserved_total()
    log_before_cancel = h.max_stock_log_id()
    h.post("/orders/202609160005/cancel", {}, 302, "店员取消 PENDING 单")
    h.check_true("状态变为 CANCELLED", h.order_row("202609160005")[1] == "CANCELLED")
    h.check_true(
        "取消释放预占（精确差值：该单 6 种原料合计 231.00）",
        reserved_before_cancel - h.reserved_total() == _d("231.00"),
        f"{reserved_before_cancel} → {h.reserved_total()}",
    )
    h.check_true(
        "取消不写库存流水（ORD-22）",
        h.max_stock_log_id() == log_before_cancel,
    )
    h.post("/orders/202609160005/cancel", {}, 409, "重复取消 E4001")
    h.logout()

    # ---- 12) 全量回滚前的账：订单数 = 基线 + 本轮新增 4 ----
    h.check_true(
        "订单数 = 基线 + 新增 4（现金 / 自助 / 平台 / 加料注入单）",
        h.order_count() == base_orders + 4,
        f"{base_orders} → {h.order_count()}",
    )


def render(h: Harness, ok: bool) -> str:
    lines: list[str] = []
    w = lines.append
    total = len(h.rows)
    passed = sum(1 for r in h.rows if r[3] == "PASS")
    w("=" * 96)
    w("第 7 周 ⑤ 订单页面自检：路由权限 + 页面渲染 + 下单/支付/取消/退款/状态")
    w("=" * 96)
    w(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    w(f"Python {sys.version.split()[0]}　DB_AUTH={Config.DB_AUTH}　DB_NAME={Config.DB_NAME}")
    w("执行方式：Flask 测试客户端（真实 HTTP 会话）+ 外层事务 + SAVEPOINT，跑完整轮回滚，seed 不变")
    w("")
    w(f"{'用例':<46}{'预期':>6}{'实际':>6}  结果  备注")
    w("-" * 96)
    for label, expect, actual, status, note in h.rows:
        w(f"{label:<46}{expect:>6}{actual:>6}  {status}  {note}")
    w("-" * 96)
    w(f"总计：{total} 项，PASS {passed}，FAIL {total - passed}")
    w("")
    w("=" * 96)
    w("结论：" + ("全部通过" if ok else "存在失败项，需修复"))
    w("=" * 96)
    return "\n".join(lines) + "\n"


def main() -> int:
    write = "--write" in sys.argv
    sql_mode = "--sql-auth" in sys.argv
    if sql_mode:
        # Config.DB_AUTH 在 import 时快照，这里直接改类属性；密码由 Config.password_for 实时读环境变量
        Config.DB_AUTH = "sql"
        for key, value in _SQL_LOGIN_PWD.items():
            os.environ.setdefault(key, value)

    h = Harness()
    try:
        run_read_cases(h)
        run_write_cases(h)
    finally:
        h.close()

    failed = [r for r in h.rows if r[3] != "PASS"]
    ok = not failed
    print(render(h, ok))
    for label, expect, actual, status, note in h.rows:
        if status != "PASS":
            print(f"[FAIL] {label}: {actual} (expect {expect}) {note}")
    if write:
        suffix = "-sql认证" if sql_mode else ""
        path = os.path.join(
            ROOT, "result", f"第7周-⑤订单页面自检-{dt.date.today():%Y%m%d}{suffix}.txt"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(render(h, ok))
        print("evidence:", os.path.relpath(path, ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
