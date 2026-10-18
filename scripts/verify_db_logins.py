"""① 自检：5 个受限 SQL 登录在数据库层的权限实测（可复跑）。

用法（在仓库根目录执行）：

    python scripts/verify_db_logins.py            # 只跑并打印
    python scripts/verify_db_logins.py --write    # 另写 result/第7周-①受限登录验证-<日期>.txt

前置：已执行 scripts/enable-mixed-auth.ps1 与 sql/app_login.sql。
密码优先从环境变量读取（DB_PWD_<LOGIN>），缺省用 sql/app_login.sql 里的示例值。

所有写语句都是 no-op（SET 列 = 列）并回滚，不改变业务数据。
退出码：0 = 全部符合预期；1 = 有不符合项。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys

import pyodbc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SERVER = os.environ.get("DB_SERVER", r".\SQLEXPRESS")
DATABASE = os.environ.get("DB_NAME", "MilkTeaShop_v01")
DRIVER = os.environ.get("DB_DRIVER", "ODBC Driver 18 for SQL Server")

_DEMO = {
    "login_clerk": "Clerk#2026",
    "login_customer": "Cust#2026",
    "login_maker": "Maker#2026",
    "login_stock": "Stock#2026",
    "login_manager": "Mgr#2026",
}
PASSWORDS = {login: os.environ.get(f"DB_PWD_{login.upper()}", pwd) or pwd
             for login, pwd in _DEMO.items()}


def connect(login: str) -> pyodbc.Connection:
    cs = (
        f"DRIVER={{{DRIVER}}};SERVER={SERVER};DATABASE={DATABASE};"
        f"UID={login};PWD={PASSWORDS[login]};Encrypt=yes;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(cs, autocommit=False, timeout=5)


def native_code(exc: BaseException) -> str:
    match = re.search(r"\((\d{3,5})\)", str(exc))
    return match.group(1) if match else "?"


def run(login: str, statements: list[str], expect: str):
    conn = None
    info = ""
    try:
        conn = connect(login)
        cur = conn.cursor()
        for sql in statements:
            cur.execute(sql)
            if cur.description is not None:
                row = cur.fetchone()
                if row is not None:
                    info = " | ".join(str(v) for v in row)
        conn.rollback()
        actual, detail = "ok", info
    except pyodbc.Error as exc:
        actual, detail = native_code(exc), str(exc).split("]")[-1].strip()[:70]
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return actual == expect, actual, detail


TESTS = [
    ("T1", "login_clerk", "读商品（应有权限）", "ok",
     ["SELECT USER_NAME(), IS_MEMBER('role_cashier'), (SELECT COUNT(*) FROM dbo.item_product)"]),
    ("T2", "login_clerk", "改库存 stock_qty（应被拒）", "229",
     ["UPDATE dbo.item_material SET stock_qty = stock_qty WHERE material_id = 'M0000001'"]),
    ("T3", "login_customer", "写订单（应被拒）", "229",
     ["UPDATE dbo.shop_order SET item_subtotal = item_subtotal WHERE order_id = '202609160001'"]),
    ("T4", "login_maker", "改 order_status（应成功，列级授权）", "ok",
     ["UPDATE dbo.shop_order SET order_status = order_status WHERE order_id = '202609160001'"]),
    ("T5", "login_maker", "改 total_amount（应被拒，列级授权外）", "230",
     ["UPDATE dbo.shop_order SET total_amount = total_amount WHERE order_id = '202609160001'"]),
    ("T6", "login_stock", "读会员表（应被拒）", "229",
     ["SELECT COUNT(*) FROM dbo.member_member"]),
    ("T7", "login_stock", "调整库存（应成功）", "ok",
     ["UPDATE dbo.item_material SET stock_qty = stock_qty WHERE material_id = 'M0000001'"]),
    ("T8", "login_manager", "建表 DDL（应被拒，业务管理≠DBA）", "262",
     ["CREATE TABLE dbo.__probe_ddl (x int)"]),
    ("T9", "login_customer", "读商品（应有权限）", "ok",
     ["SELECT USER_NAME(), IS_MEMBER('role_customer'), (SELECT COUNT(*) FROM dbo.item_product)"]),
    ("T10", "login_manager", "读库存 item_material（权限矩阵 inventory.read）", "ok",
     ["SELECT COUNT(*) FROM dbo.item_material"]),
    ("T11", "login_clerk", "读销量统计视图", "ok",
     ["SELECT COUNT(*) FROM dbo.v_product_sales_summary"]),
    ("T12", "login_customer", "读销量统计视图（应被拒，未授权）", "229",
     ["SELECT COUNT(*) FROM dbo.v_product_sales_summary"]),
    ("T13", "login_customer", "自助下单：写订单头+明细+预占（应成功）", "ok",
     ["INSERT INTO dbo.shop_order (order_id, channel, order_mode, order_status, member_id, employee_id, "
      "item_subtotal, coupon_discount, total_amount, pay_method, created_at) "
      "VALUES ('209912319999','COUNTER','COUNTER_SELF','PENDING','C0000001','E0000001',12.00,0.00,12.00,NULL,'2099-12-31 12:00:00')",
      "INSERT INTO dbo.order_item (order_id, item_line, product_id, size, sugar_level, ice_level, "
      "temp_level, qty, unit_price) "
      "VALUES ('209912319999',1,'P0000001','MEDIUM','FULL','REGULAR','COLD',1,12.00)",
      "UPDATE dbo.item_material SET reserved_qty = reserved_qty + 30 WHERE material_id = 'M0000001'"]),
    ("T14", "login_customer", "改 stock_qty（应被拒：只授到 reserved_qty，列级报 230）", "230",
     ["UPDATE dbo.item_material SET stock_qty = stock_qty WHERE material_id = 'M0000001'"]),
    ("T15", "login_clerk", "改订单状态（应成功，在收窄后的 8 列内）", "ok",
     ["UPDATE dbo.shop_order SET order_status = order_status WHERE order_id = '202609160001'"]),
    ("T16", "login_clerk", "写退款额 refund_amount（应被拒：归店长审核，230）", "230",
     ["UPDATE dbo.shop_order SET refund_amount = refund_amount WHERE order_id = '202609160001'"]),
    ("T17", "login_manager", "写退款额 refund_amount（应成功：店长审核退款）", "ok",
     ["UPDATE dbo.shop_order SET refund_amount = refund_amount WHERE order_id = '202609160001'"]),
]


def main() -> int:
    write = "--write" in sys.argv
    rows = []
    ok_all = True
    for tid, login, desc, expect, statements in TESTS:
        passed, actual, detail = run(login, statements, expect)
        ok_all = ok_all and passed
        rows.append((tid, login, desc, expect, actual, "PASS" if passed else "FAIL", detail))
        print(f"{tid} {login} {desc} expect={expect} actual={actual} {'PASS' if passed else 'FAIL'}")

    if write:
        lines = []
        w = lines.append
        w("=" * 96)
        w("第 7 周 ① 证据：混合认证 + 5 个受限 SQL 登录的数据库级权限实测")
        w("=" * 96)
        w(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
        w(f"实例：{SERVER}　库：{DATABASE}　驱动：{DRIVER}")
        w("前提：scripts/enable-mixed-auth.ps1 + sql/app_login.sql 已执行（混合模式）")
        w("说明：所有写语句均为 no-op（SET 列 = 列）并在结束前回滚，不改变业务数据。")
        w("")
        w(f"{'#':<5}{'登录':<17}{'测试内容':<40}{'预期':<6}{'实际':<6}结果")
        w("-" * 96)
        for tid, login, desc, expect, actual, status, _ in rows:
            w(f"{tid:<5}{login:<17}{desc:<40}{expect:<6}{actual:<6}{status}")
        w("-" * 96)
        w("错误码：229 = 对象/列权限不足；230 = 列级权限不足；262 = 无 DDL 权限")
        w("")
        w("逐条明细：")
        for tid, login, desc, expect, actual, status, detail in rows:
            w(f"  {tid} {login} — {desc}")
            w(f"      预期 {expect} / 实际 {actual} → {status}")
            if detail:
                w(f"      返回：{detail}")
        w("")
        w("=" * 96)
        w("结论：" + ("全部符合预期（受限账户在数据库层确实生效）" if ok_all else "存在不符合项，需排查"))
        w("=" * 96)
        name = f"第7周-①受限登录验证-{dt.date.today():%Y%m%d}.txt"
        path = os.path.join(ROOT, "result", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print("evidence:", os.path.relpath(path, ROOT))

    print("\nRESULT:", "ALL PASSED" if ok_all else "FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
