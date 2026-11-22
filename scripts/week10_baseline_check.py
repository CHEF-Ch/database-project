"""第十周 ① 基线核对（只读）：v1.0 结构指纹 + 全部表行数 + 受限登录权限。

用法（在仓库根目录执行）：

    python scripts/week10_baseline_check.py            # 只打印
    python scripts/week10_baseline_check.py --write    # 另写 result/第10周-①基线核对.txt

只做 SELECT / 系统目录查询，不改任何数据。指纹口径与 /health 探针一致（app/dao/connection.py）。
"""
from __future__ import annotations

import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.config import Config  # noqa: E402
from app.dao.connection import V1_FINGERPRINT, _FINGERPRINT_SQL, db_cursor  # noqa: E402

_BUSINESS_USERS = (
    "login_clerk", "login_customer", "login_maker", "login_stock", "login_manager", "login_app",
)


def _fingerprint(cur) -> dict:
    cur.execute(_FINGERPRINT_SQL)
    r = cur.fetchone()
    parts = {
        "columns": int(r.columns), "pk": int(r.pk), "uq": int(r.uq),
        "uq_filtered": int(r.uq_filtered), "fk": int(r.fk),
        "check": int(r.ck), "default": int(r.df),
    }
    parts["total"] = sum(parts.values())
    parts["expected_v1_0"] = V1_FINGERPRINT
    parts["match"] = parts["total"] == V1_FINGERPRINT
    return parts


def _table_rows(cur) -> list[tuple[str, int]]:
    """所有用户表的精确行数（按表名排序）。"""
    cur.execute("SELECT name FROM sys.tables ORDER BY name")
    tables = [r.name for r in cur.fetchall()]
    rows: list[tuple[str, int]] = []
    for name in tables:
        cur.execute(f"SELECT COUNT(*) FROM dbo.[{name}]")
        rows.append((name, int(cur.fetchone()[0])))
    return rows


def _principals(cur) -> tuple[list, list]:
    """业务数据库用户是否存在，及其角色成员关系。"""
    cur.execute(
        "SELECT name, type_desc FROM sys.database_principals "
        "WHERE name IN (?,?,?,?,?,?,?) ORDER BY name",
        *_BUSINESS_USERS, "role_customer",
    )
    users = [(r.name, r.type_desc) for r in cur.fetchall()]

    cur.execute(
        "SELECT r.name AS role_name, m.name AS member_name "
        "FROM sys.database_role_members rm "
        "JOIN sys.database_principals r ON rm.role_principal_id = r.principal_id "
        "JOIN sys.database_principals m ON rm.member_principal_id = m.principal_id "
        "WHERE m.name IN (?,?,?,?,?,?,?) "
        "ORDER BY r.name, m.name",
        *_BUSINESS_USERS, "role_customer",
    )
    memberships = [(r.role_name, r.member_name) for r in cur.fetchall()]
    return users, memberships


def _render(fp, tables, users, memberships) -> str:
    w = []
    a = w.append
    a("=" * 90)
    a("第 10 周 ① 基线核对证据：v1.0 结构指纹 + 表行数 + 受限登录权限（只读）")
    a("=" * 90)
    a(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    a(f"连接：DB_AUTH={Config.DB_AUTH}  DB_NAME={Config.DB_NAME}  DB_SERVER={Config.DB_SERVER}")
    a("")
    a("【1】结构指纹（与 /health 同口径，第六周声明 v1.0 = 236）")
    a("-" * 90)
    a(f"列 {fp['columns']} + 主码 {fp['pk']} + UQ {fp['uq']} + 筛选唯一索引 {fp['uq_filtered']} "
      f"+ 外码 {fp['fk']} + CHECK {fp['check']} + DEFAULT {fp['default']} = {fp['total']}")
    a(f"期望 {fp['expected_v1_0']}，匹配：{'是' if fp['match'] else '否（⚠ 指纹不一致）'}")
    a("")
    a("【2】全部用户表行数（样例数据核对）")
    a("-" * 90)
    total = 0
    for name, cnt in tables:
        a(f"  {name:<24} {cnt}")
        total += cnt
    a(f"  共 {len(tables)} 张表，合计 {total} 行")
    a("")
    a("【3】受限登录 / 数据库用户（是否存在）")
    a("-" * 90)
    for name, typ in users:
        a(f"  {name:<24} {typ}")
    missing = [u for u in _BUSINESS_USERS if u not in {n for n, _ in users}]
    if "role_customer" not in {n for n, _ in users}:
        missing.append("role_customer")
    a(f"  缺失：{missing if missing else '无'}")
    a("")
    a("【4】角色成员关系（用户 → 数据库角色）")
    a("-" * 90)
    for role, member in memberships:
        a(f"  {member:<24} → {role}")
    if not memberships:
        a("  （无）")
    a("")
    a("【5】结论")
    a("-" * 90)
    ok = fp["match"] and len(tables) == 17 and not missing
    a("PASS：指纹 236、17 张表、业务登录齐全" if ok else "FAIL：存在不一致（见上）")
    a("=" * 90)
    return "\n".join(w) + "\n"


def main() -> int:
    with db_cursor(None) as cur:
        cur.execute("SELECT DB_NAME() AS db")
        db_name = cur.fetchone().db
        fp = _fingerprint(cur)
        tables = _table_rows(cur)
        users, memberships = _principals(cur)

    text = _render(fp, tables, users, memberships)
    print(text)

    missing = [u for u in _BUSINESS_USERS if u not in {n for n, _ in users}]
    if "role_customer" not in {n for n, _ in users}:
        missing.append("role_customer")
    ok = fp["match"] and len(tables) == 17 and not missing
    print("RESULT:", "PASS" if ok else "FAIL", f"(库={db_name}, 指纹={fp['total']})")

    if "--write" in sys.argv:
        path = os.path.join(ROOT, "result", "第10周-①基线核对.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("evidence:", os.path.relpath(path, ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
