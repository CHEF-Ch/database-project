"""第十一周 ① 基线核对（只读）：v2.2 结构指纹、对象清单、库存/订单基线。

用法（在仓库根目录执行，DB_NAME 指向 week11 验收库）：

    $env:DB_NAME = 'MilkTeaShop_week11_baseline'
    python scripts/week11_baseline_check.py            # 只打印
    python scripts/week11_baseline_check.py --write    # 另写 result/第11周-①基线核对.txt

只做 SELECT / 系统目录查询，不改任何数据。预期基线：
  · schema_version = 2.2，指纹 256（121 列 / 19 PK / 3 UQ + 4 筛选唯一 / 29 FK / 63 CHECK / 17 DEFAULT）
  · 触发器 0 个、inv_stock_audit 不存在（本周对象的迁移前状态）
  · 库存与订单行数快照，用于 ⑤ 回归时对比
"""
from __future__ import annotations

import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import Config  # noqa: E402
from app.dao.connection import V22_SCHEMA_PARTS, _FINGERPRINT_SQL, db_cursor  # noqa: E402


def _fingerprint(cur) -> dict:
    cur.execute(_FINGERPRINT_SQL)
    r = cur.fetchone()
    parts = {
        "columns": int(r.columns), "pk": int(r.pk), "uq": int(r.uq),
        "uq_filtered": int(r.uq_filtered), "fk": int(r.fk),
        "check": int(r.ck), "default": int(r.df),
    }
    parts["total"] = sum(parts.values())
    parts["expected"] = sum(V22_SCHEMA_PARTS.values())
    parts["match"] = all(parts[k] == v for k, v in V22_SCHEMA_PARTS.items())
    return parts


def _table_rows(cur) -> list[tuple[str, int]]:
    cur.execute("SELECT name FROM sys.tables ORDER BY name")
    names = [row.name for row in cur.fetchall()]
    rows: list[tuple[str, int]] = []
    for name in names:
        cur.execute(f"SELECT COUNT(*) FROM dbo.[{name}]")
        rows.append((name, int(cur.fetchone()[0])))
    return rows


def _objects(cur) -> tuple[int, list[str], int, int]:
    cur.execute("SELECT name FROM sys.triggers ORDER BY name")
    triggers = [row.name for row in cur.fetchall()]
    cur.execute(
        "SELECT OBJECT_ID('dbo.inv_stock_audit', 'U') AS audit_table, "
        "(SELECT COUNT(*) FROM sys.objects WHERE type IN ('FN', 'IF') "
        " AND name IN ('fn_product_sales_rank', 'fn_daily_sales_cumulative', 'fn_daily_sales_moving_avg')) "
        "AS window_functions"
    )
    row = cur.fetchone()
    return len(triggers), triggers, (1 if row.audit_table is not None else 0), int(row.window_functions)


def _stock(cur) -> list[tuple]:
    cur.execute(
        "SELECT material_id, name, stock_qty, reserved_qty, safety_stock "
        "FROM dbo.item_material ORDER BY material_id"
    )
    return [tuple(row) for row in cur.fetchall()]


def _orders(cur) -> list[tuple]:
    cur.execute(
        "SELECT order_status, COUNT(*) AS order_count, SUM(total_amount) AS amount "
        "FROM dbo.shop_order GROUP BY order_status ORDER BY order_status"
    )
    return [tuple(row) for row in cur.fetchall()]


def _sale_logs(cur) -> int:
    cur.execute("SELECT COUNT(*) FROM dbo.inv_stock_log WHERE log_type = 'SALE'")
    return int(cur.fetchone()[0])


def _render(meta: dict) -> tuple[str, bool]:
    fp = meta["fingerprint"]
    w: list[str] = []
    a = w.append
    a("=" * 96)
    a("第 11 周 ① 基线核对证据：v2.2 结构指纹 / 对象清单 / 库存与订单基线（只读）")
    a("=" * 96)
    a(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    a(f"连接：DB_AUTH={Config.DB_AUTH}  DB_NAME={Config.DB_NAME}  DB_SERVER={Config.DB_SERVER}")
    a(f"数据库：{meta['db']}    版本：{meta['version']}")
    a("")
    a("【1】结构指纹（与 /health 同口径；v2.2 期望 256）")
    a("-" * 96)
    a(f"列 {fp['columns']} + 主码 {fp['pk']} + UQ {fp['uq']} + 筛选唯一索引 {fp['uq_filtered']} "
      f"+ 外码 {fp['fk']} + CHECK {fp['check']} + DEFAULT {fp['default']} = {fp['total']}")
    a(f"期望 {fp['expected']}，匹配：{'是' if fp['match'] else '否（⚠ 指纹不一致）'}")
    a("")
    a("【2】全部用户表行数（迁移前快照）")
    a("-" * 96)
    total = 0
    for name, cnt in meta["tables"]:
        a(f"  {name:<24} {cnt}")
        total += cnt
    a(f"  共 {len(meta['tables'])} 张表，合计 {total} 行")
    a("")
    a("【3】本周对象迁移前状态（预期：触发器 0、审计表不存在）")
    a("-" * 96)
    a(f"  触发器 {meta['trigger_count']} 个：{meta['triggers'] or '（无）'}")
    a(f"  inv_stock_audit 存在：{'是' if meta['audit_table'] else '否'}")
    a(f"  窗口销售函数 {meta['window_functions']} 个（本周新增，迁移前预期 0）")
    a("")
    a("【4】库存基线（⑤ 回归对比用）")
    a("-" * 96)
    for material_id, name, stock_qty, reserved_qty, safety_stock in meta["stock"]:
        a(f"  {material_id} {name:<10} 现存 {stock_qty:>10} 预占 {reserved_qty:>10} 安全 {safety_stock:>8}")
    a(f"  SALE 流水 {meta['sale_logs']} 行")
    a("")
    a("【5】订单基线（状态 × 单数 × 金额）")
    a("-" * 96)
    for status, count, amount in meta["orders"]:
        a(f"  {status:<10} {count:>4} 单  {amount}")
    a("")
    a("【6】结论")
    a("-" * 96)
    ok = (
        meta["version"] == "2.2"
        and fp["match"]
        and len(meta["tables"]) == 19
        and meta["trigger_count"] == 0
        and meta["audit_table"] == 0
    )
    a("PASS：v2.2 指纹 256、19 张表、无触发器/审计表（迁移前基线可复现）"
      if ok else "FAIL：基线不符合预期（见上）")
    a("=" * 96)
    return "\n".join(w) + "\n", ok


def main() -> int:
    with db_cursor(None) as cur:
        cur.execute(
            "SELECT DB_NAME() AS db, "
            "CONVERT(nvarchar(20), value) AS version "
            "FROM sys.extended_properties WHERE class = 0 AND name = N'milktea_schema_version'"
        )
        head = cur.fetchone()
        db_name = head.db
        version = head.version or "1.0"
        fp = _fingerprint(cur)
        tables = _table_rows(cur)
        trigger_count, triggers, audit_table, window_functions = _objects(cur)
        stock = _stock(cur)
        orders = _orders(cur)
        sale_logs = _sale_logs(cur)

    meta = {
        "db": db_name, "version": version, "fingerprint": fp, "tables": tables,
        "trigger_count": trigger_count, "triggers": triggers,
        "audit_table": audit_table, "window_functions": window_functions,
        "stock": stock, "orders": orders, "sale_logs": sale_logs,
    }
    text, ok = _render(meta)
    print(text)
    print("RESULT:", "PASS" if ok else "FAIL", f"(库={db_name}, 版本={version}, 指纹={fp['total']})")

    if "--write" in sys.argv:
        path = os.path.join(ROOT, "result", "第11周-①基线核对.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("evidence:", os.path.relpath(path, ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
