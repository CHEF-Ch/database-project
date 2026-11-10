"""⑥ 权限矩阵自检：静态收口 + 行为收口（可复跑）。

把三处「权限的事实来源」钉到一致，任何一处漂移即 FAIL：

  1. ``app/services/permissions.py`` 的 ``PERMISSIONS``（唯一事实来源）
  2. ``app/pages/*.py`` 路由装饰器 ``@role_required`` / ``@role_required_any``
  3. ``docs/09-应用开发/结构与运行.md`` §8 的权限矩阵（固化为下方 ``SPEC``）

两部分：

  - **静态收口**（不依赖数据库）：矩阵逐格 == 规范矩阵；路由接线权限 ⊆ 已知权限；
    未接线权限 == 白名单 ``{coupon.write}``（券维护页留 v2.0）。
  - **行为收口**（该有则有 / 该无则无）：对每个权限点的代表端点，5 角色全扫。
    拒绝侧（非授权角色 → 403）不碰 DB；放行侧只测 GET 端点、且 ``/health`` 探到
    DB 可用才跑，否则整段标 SKIP。

用法（在仓库根目录执行）：

    python scripts/permission_matrix_test.py             # 只跑并打印
    python scripts/permission_matrix_test.py --write     # 写 result/第7周-⑥权限矩阵自检-<日期>.txt
    python scripts/permission_matrix_test.py --sql-auth --write   # 受限登录下再验（放行侧走各角色 DB 读授权）

退出码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import ast
import datetime as dt
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app
from _csrf_client import make_client                                  # noqa: E402
from app.config import Config                               # noqa: E402
from app.services.permissions import PERMISSIONS, ROLE_LABELS  # noqa: E402

# 规范矩阵（= docs/09 §8 的 5 角色 × 11 权限点）。代码里的 PERMISSIONS 一旦与它
# 不一致（多一格/少一格/拼写不同）都算 FAIL。
SPEC: dict[str, set[str]] = {
    "clerk": {
        "product.read", "inventory.read", "order.create",
        "order.read", "order.read_own",
    },
    "customer": {"product.read", "order.create", "order.read_own"},
    "maker": {"product.read", "order.read", "order.update_status"},
    "stock": {"product.read", "inventory.read", "inventory.adjust", "restock.create"},
    "manager": {
        "product.read", "product.write", "inventory.read",
        "order.read", "order.refund", "coupon.write",
    },
}

# 矩阵里存在、但 v1.0 没有任何路由入口的权限点（券维护页留 v2.0，券只走订单流程）。
DEAD_PERMISSION_ALLOWLIST: frozenset[str] = frozenset({"coupon.write"})

PAGES_DIR = ROOT / "app" / "pages"

# 行为收口用的 demo 账号（明文密码同 README / docs/09 §7 的演示账号表）
DEMO_ACCOUNTS: dict[str, tuple[str, str]] = {
    "clerk": ("clerk1", "clerk123"),
    "customer": ("customer1", "cust123"),
    "maker": ("maker1", "maker123"),
    "stock": ("stock1", "stock123"),
    "manager": ("manager1", "mgr123"),
}

# 每个权限点的代表端点 (method, path)。coupon.write 无路由入口 → None。
# POST 型端点只做“拒绝侧”（403 在进视图前拦下，不会真写库）。
REPRESENTATIVE: dict[str, tuple[str, str] | None] = {
    "product.read": ("GET", "/products/"),
    "product.write": ("GET", "/products/new"),
    "inventory.read": ("GET", "/inventory/"),
    "inventory.adjust": ("POST", "/inventory/adjust"),
    "restock.create": ("GET", "/inventory/restocks"),
    "order.read": ("GET", "/orders/"),
    "order.read_own": ("GET", "/orders/mine"),
    "order.create": ("GET", "/orders/new"),
    "order.refund": ("POST", "/orders/1/refund"),
    "order.update_status": ("POST", "/orders/1/status"),
    "coupon.write": None,
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

ALL_PERMS: set[str] = set().union(*SPEC.values())


def wired_permissions() -> set[str]:
    """扫描 app/pages/*.py，收集装饰器里引用的权限点字符串（AST，非正则）。"""
    found: set[str] = set()
    for path in sorted(PAGES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) \
                        and dec.func.id in {"role_required", "role_required_any"}:
                    for arg in dec.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            found.add(arg.value)
    return found


def _fmt(perms: set[str]) -> str:
    return ", ".join(sorted(perms)) if perms else "（空）"


# ---------------------------------------------------------------------------
# 静态收口
# ---------------------------------------------------------------------------
def run_static_checks() -> tuple[list[tuple[str, str, str, str]], bool]:
    known = set().union(*PERMISSIONS.values()) if PERMISSIONS else set()
    spec_known = ALL_PERMS
    wired = wired_permissions()

    rows: list[tuple[str, str, str, str]] = []
    ok = True

    def add(label, expect, actual, passed):
        nonlocal ok
        if not passed:
            ok = False
        rows.append((label, expect, actual, "PASS" if passed else "FAIL"))

    roles_code = set(PERMISSIONS)
    roles_labels = set(ROLE_LABELS)
    roles_spec = set(SPEC)
    roles_same = roles_code == roles_labels == roles_spec
    add(
        "角色集合一致（PERMISSIONS=ROLE_LABELS=SPEC）",
        _fmt(roles_spec),
        "一致" if roles_same else f"code={_fmt(roles_code)} label={_fmt(roles_labels)}",
        roles_same,
    )

    diffs: list[str] = []
    for role in sorted(roles_spec | roles_code):
        spec = SPEC.get(role, set())
        code = PERMISSIONS.get(role, set())
        if sorted(spec - code):
            diffs.append(f"{role} 缺 {sorted(spec - code)}")
        if sorted(code - spec):
            diffs.append(f"{role} 多 {sorted(code - spec)}")
    add(
        "矩阵逐格 == 规范矩阵",
        "5 角色 × 11 权限点全一致",
        "一致" if not diffs else "；".join(diffs),
        not diffs,
    )

    unknown_wired = sorted(wired - known)
    add(
        "路由接线权限 ⊆ 已知权限",
        "无未注册权限",
        _fmt(set(unknown_wired)),
        not unknown_wired,
    )

    dead = known - wired
    unexpected = sorted(dead - DEAD_PERMISSION_ALLOWLIST)
    missing_dead = sorted(DEAD_PERMISSION_ALLOWLIST - dead)
    detail = ""
    if unexpected:
        detail += f" 意外未接线 {unexpected}"
    if missing_dead:
        detail += f" 白名单项却已接线 {missing_dead}"
    add(
        "未接线权限 == 白名单 {coupon.write}",
        "coupon.write 仅此一项（券维护页留 v2.0）",
        "符合" if not detail else detail,
        not unexpected and not missing_dead,
    )

    return rows, ok


# ---------------------------------------------------------------------------
# 行为收口
# ---------------------------------------------------------------------------
def _login(client, role: str) -> int:
    username, password = DEMO_ACCOUNTS[role]
    resp = client.post("/login", data={"username": username, "password": password})
    return resp.status_code


def db_available(client) -> bool:
    """/health 探针：DB 可用且结构指纹与 v1.0 一致才返回 True。"""
    try:
        resp = client.get("/health")
        if resp.status_code != 200:
            return False
        payload = resp.get_json() or {}
        fp = payload.get("fingerprint") or {}
        return bool(fp.get("match")) and fp.get("total") == fp.get("expected_v1_0")
    except Exception:
        return False


def run_behavior_checks(client) -> tuple[list[tuple[str, int, int, str, str]], bool]:
    """返回 (rows, allowed_ok)。

    - 拒绝侧：对每个非授权角色命中代表端点，断言 403（不碰 DB）。
    - 放行侧：仅 GET 代表端点 + DB 可用时，对授权角色断言 200。
    """
    rows: list[tuple[str, int, int, str, str]] = []
    allowed_ok = db_available(client)

    for perm in sorted(ALL_PERMS):
        rep = REPRESENTATIVE.get(perm)
        if rep is None:
            continue
        method, path = rep
        granted = {role for role, perms in SPEC.items() if perm in perms}
        for role in sorted(SPEC):
            login_status = _login(client, role)
            if login_status != 302:
                rows.append((f"登录 {role}（应 302）", 302, login_status, "FAIL", ""))
                continue
            if role not in granted:
                # 拒绝侧：403 由 role_required 在进视图前拦下，不碰 DB，安全
                resp = client.get(path) if method == "GET" else client.post(path)
                rows.append((
                    f"{perm} × {role}（拒绝→403）", 403, resp.status_code,
                    "PASS" if resp.status_code == 403 else "FAIL", f"{method} {path}",
                ))
            elif method == "GET" and allowed_ok:
                # 放行侧：只测 GET；POST 型端点不发起请求，避免真写库
                resp = client.get(path)
                rows.append((
                    f"{perm} × {role}（放行→200）", 200, resp.status_code,
                    "PASS" if resp.status_code == 200 else "FAIL", f"{method} {path}",
                ))
            # 其余（granted 且 POST，或 GET 但 DB 不可用）：不请求、不断言

    if not allowed_ok:
        rows.append(("放行侧 GET 全量（DB 不可用跳过）", "-", "-", "SKIP", "/health 未通过或指纹不匹配"))
    return rows, allowed_ok


# ---------------------------------------------------------------------------
# 渲染与入口
# ---------------------------------------------------------------------------
def render(static_rows, behavior_rows, allowed_ok, sql_mode) -> str:
    lines = []
    w = lines.append
    w("=" * 96)
    w("第 7 周 ⑥ 权限矩阵自检：静态收口 + 行为收口")
    w("=" * 96)
    w(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    w(f"认证模式：DB_AUTH={'sql' if sql_mode else 'trusted'}　DB_NAME={Config.DB_NAME}")
    w("")
    w("【1】规范矩阵（docs/09 §8 固化，5 角色 × 11 权限点）")
    w("-" * 96)
    for role in sorted(SPEC):
        w(f"  {role:<9}: {_fmt(SPEC[role])}")
    w("")
    w("【2】代码内矩阵（app/services/permissions.py）")
    w("-" * 96)
    for role in sorted(PERMISSIONS):
        w(f"  {role:<9}: {_fmt(PERMISSIONS[role])}")
    w("")
    w("【3】路由已接线的权限点（AST 扫 app/pages/*.py）")
    w("-" * 96)
    w(f"  {_fmt(wired_permissions())}")
    w("")
    w("【4】静态检查项")
    w("-" * 96)
    w(f"{'检查':<40}{'预期 / 实际':<52}结果")
    for label, expect, actual, status in static_rows:
        w(f"{label:<40}{expect + ' / ' + actual:<52}{status}")
    w("")
    w("【5】行为收口（拒绝侧不碰 DB；放行侧 GET 且 DB 可用才跑）")
    w("-" * 96)
    w(f"放行侧状态：{'DB 可用，已全量跑' if allowed_ok else 'DB 不可用，已标 SKIP'}")
    w(f"{'用例':<46}{'预期':>6}{'实际':>6}  结果  端点")
    for label, expect, actual, status, note in behavior_rows:
        w(f"{label:<46}{str(expect):>6}{str(actual):>6}  {status}  {note}")
    w("")
    w("=" * 96)
    all_ok = all(r[3] == "PASS" for r in static_rows) and all(
        r[3] in ("PASS", "SKIP") for r in behavior_rows
    )
    w("结论：" + ("全部通过" if all_ok else "存在失败项，需修复"))
    w("=" * 96)
    return "\n".join(lines) + "\n"


def main() -> int:
    write = "--write" in sys.argv
    sql_mode = "--sql-auth" in sys.argv
    if sql_mode:
        # Config.DB_AUTH 在 import 时快照，这里直接改类属性；密码由 Config 实时读环境变量
        Config.DB_AUTH = "sql"
        for key, value in _SQL_LOGIN_PWD.items():
            os.environ.setdefault(key, value)

    static_rows, static_ok = run_static_checks()
    app = create_app()
    client = make_client(app)
    behavior_rows, allowed_ok = run_behavior_checks(client)

    ok = static_ok and all(r[3] in ("PASS", "SKIP") for r in behavior_rows)

    for label, expect, actual, status in static_rows:
        if status != "PASS":
            print(f"[FAIL] {label}: 预期 {expect} / 实际 {actual}")
    for label, expect, actual, status, note in behavior_rows:
        if status == "FAIL":
            print(f"[FAIL] {label}: {actual} (expect {expect}) {note}")
    print()
    print("RESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    denied = sum(1 for r in behavior_rows if "拒绝" in r[0])
    allowed = sum(1 for r in behavior_rows if "放行" in r[0])
    print(f"行为收口：拒绝侧 {denied} 项 / 放行侧 {allowed} 项 / DB可用={allowed_ok}")

    if write:
        suffix = "-sql认证" if sql_mode else ""
        name = f"第7周-⑥权限矩阵自检-{dt.date.today():%Y%m%d}{suffix}.txt"
        path = ROOT / "result" / name
        path.write_text(render(static_rows, behavior_rows, allowed_ok, sql_mode), encoding="utf-8")
        print("evidence:", os.path.relpath(path, ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
