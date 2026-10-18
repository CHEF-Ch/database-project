"""① 自检：v1.0 基线 + 分层骨架用例 + 统一异常（可复跑）。

用法（在仓库根目录执行）：

    python scripts/smoke_test.py                  # 只跑并打印
    python scripts/smoke_test.py --write          # 另写 result/第7周-①②-骨架自检-<日期>.txt
    python scripts/smoke_test.py --write --real-server
                                                  # 额外起一次真实 HTTP 服务再验

退出码：0 = 全部通过；1 = 有失败项。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from importlib.metadata import version

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import create_app                      # noqa: E402
from app.config import Config                   # noqa: E402
from app.errors import classify_db_error        # noqa: E402

# 回归用例：曾因 "262" 是 "2627" 的子串而把唯一键冲突误判为 403，
# 故用“括号里的消息号精确匹配”的分类必须长期有回归测试。
_CLASSIFY_CASES = [
    ("(2627) 主键/唯一约束冲突", "(2627)", "E5002"),
    ("(2601) 筛选唯一索引冲突", "(2601)", "E5002"),
    ("(547) CHECK/外键冲突", "(547)", "E5002"),
    ("(262) 无 DDL 权限", "(262)", "E2003"),
    ("(229) 对象权限不足", "(229)", "E2003"),
    ("(230) 列级权限不足", "(230)", "E2003"),
    ("(18456) 登录失败", "(18456)", "E5001"),
    ("[23000] SQLSTATE 完整性违反", "[23000]", "E5002"),
]


def classify_checks():
    rows = []
    for label, token, expect in _CLASSIFY_CASES:
        err = classify_db_error(Exception(f"[42000] [Microsoft][SQL Server] error {token}"))
        rows.append((label, expect, err.code, "PASS" if err.code == expect else "FAIL"))
    return rows


def build_cases(client):
    """返回 (label, resp, expected_status) 列表。"""
    return [
        ("GET /health", client.get("/health"), 200),
        ("GET / （未登录）", client.get("/"), 200),
        ("GET /products/ （未登录）", client.get("/products/"), 401),
        ("GET /inventory/ （未登录）", client.get("/inventory/"), 401),
        ("GET /orders/ （未登录）", client.get("/orders/"), 401),
        ("POST /login 密码错误", client.post("/login", data={"username": "clerk1", "password": "bad"}), 401),
        ("POST /login 缺字段", client.post("/login", data={"username": "", "password": ""}), 400),
        ("POST /login 超长用户名", client.post("/login", data={"username": "x" * 31, "password": "p"}), 400),
        ("POST /login clerk1", client.post("/login", data={"username": "clerk1", "password": "clerk123"}), 302),
        ("GET /products/ （店员）", client.get("/products/"), 200),
        ("GET /inventory/ （店员）", client.get("/inventory/"), 200),
        ("GET /orders/ （店员）", client.get("/orders/"), 200),
        ("GET /logout", client.get("/logout"), 302),
        ("POST /login customer1", client.post("/login", data={"username": "customer1", "password": "cust123"}), 302),
        ("GET /products/ （顾客）", client.get("/products/"), 200),
        ("GET /inventory/ （顾客）", client.get("/inventory/"), 403),
        ("GET /orders/ （顾客）", client.get("/orders/"), 403),
        ("GET /logout", client.get("/logout"), 302),
        ("POST /login manager1", client.post("/login", data={"username": "manager1", "password": "mgr123"}), 302),
        ("GET /orders/ （店长）", client.get("/orders/"), 200),
        ("GET /inventory/ （店长）", client.get("/inventory/"), 200),
        ("POST /login maker1", client.post("/login", data={"username": "maker1", "password": "maker123"}), 302),
        ("GET /inventory/ （制作员）", client.get("/inventory/"), 403),
        ("GET /logout", client.get("/logout"), 302),
        ("POST /login stock1", client.post("/login", data={"username": "stock1", "password": "stock123"}), 302),
        ("GET /orders/ （库存）", client.get("/orders/"), 403),
        ("GET /nope （404）", client.get("/nope"), 404),
        ("POST /login （405：GET 路由用 POST）", client.post("/logout"), 405),
    ]


def probe_health(client):
    resp = client.get("/health")
    body = resp.get_data(as_text=True)
    try:
        return resp, json.loads(body)
    except ValueError:
        return resp, {}


def run_client_checks():
    app = create_app()
    client = app.test_client()

    health_resp, health = probe_health(client)
    rows = []
    for label, resp, expect in build_cases(client):
        body = resp.get_data(as_text=True)
        note = ""
        if body.strip().startswith("{"):
            try:
                payload = json.loads(body)
                note = payload.get("code") or (
                    f"fingerprint={payload['fingerprint']['total']}"
                    if "fingerprint" in payload
                    else ""
                )
            except ValueError:
                pass
        rows.append((label, expect, resp.status_code, "PASS" if resp.status_code == expect else "FAIL", note))

    # 失败路径：库名指向不存在的库 → 503 且不泄露连接信息
    original = Config.DB_NAME
    Config.DB_NAME = "NoSuchDatabase_xyz"
    try:
        bad_client = create_app().test_client()
        bad_resp = bad_client.get("/health")
        bad_body = bad_resp.get_data(as_text=True)
    finally:
        Config.DB_NAME = original
    leak = any(k in bad_body for k in ("NoSuchDatabase_xyz", "DRIVER", "SQLEXPRESS", "Trusted_Connection"))
    rows.append(("GET /health （库不存在）", 503, bad_resp.status_code,
                 "PASS" if bad_resp.status_code == 503 else "FAIL", "E5001"))

    return rows, health, bad_resp, bad_body, leak


def run_real_server(port: int = 5017):
    env = os.environ.copy()
    env["APP_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "run.py"], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results = []
    try:
        base = f"http://127.0.0.1:{port}"
        health = None
        for _ in range(60):
            time.sleep(0.25)
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=1) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                break
            except Exception:                     # 服务尚未就绪
                continue
        if health is None:
            return [("真实启动", "超时未就绪", "-", "FAIL", "")]

        results.append(("GET /health", 200, 200, "PASS",
                        f"fingerprint={health['fingerprint']['total']}"))
        for path, expect in (("/", 200), ("/login", 200), ("/products/", 401)):
            try:
                with urllib.request.urlopen(f"{base}{path}", timeout=3) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            results.append((f"GET {path}", expect, code, "PASS" if code == expect else "FAIL", ""))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def render(rows, health, bad_resp, bad_body, leak, real_rows, classify_rows, config):
    fp = health.get("fingerprint", {})
    lines = []
    w = lines.append
    w("=" * 90)
    w("第 7 周 ①② 骨架自检证据：v1.0 基线 + 运行环境 + 分层骨架")
    w("=" * 90)
    w(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    w(f"Python {sys.version.split()[0]}　Flask {version('flask')}　"
      f"pyodbc {version('pyodbc')}　Werkzeug {version('werkzeug')}")
    w(f"连接配置：DB_AUTH={config.DB_AUTH}　DB_NAME={config.DB_NAME}　USER_STORE={config.USER_STORE}")
    w("")
    w("【1】v1.0 基线（/health 只读探针）")
    w("-" * 90)
    w(json.dumps(health, ensure_ascii=False, indent=2))
    if fp:
        w("")
        w(f"结论：指纹 {fp['total']} = {fp['columns']} 列 + {fp['pk']} 主码 + {fp['uq']} UQ + "
          f"{fp['uq_filtered']} 筛选唯一索引 + {fp['fk']} 外码 + {fp['check']} CHECK + "
          f"{fp['default']} DEFAULT；与第六周声明的 236 一致：{fp['match']}")
    w("")
    w("【2】分层骨架用例（页面层 → 权限矩阵 → 统一异常）")
    w("-" * 90)
    w(f"{'用例':<40}{'预期':>6}{'实际':>6}  结果  备注")
    for label, expect, actual, status, note in rows:
        w(f"{label:<40}{expect:>6}{actual:>6}  {status}  {note}")
    w("")
    w("【3】失败路径：数据库连接失败（库名指向不存在的库）")
    w("-" * 90)
    w(f"HTTP {bad_resp.status_code}（预期 503）")
    w(f"响应体：{bad_body.strip()}")
    w(f"是否泄露库名/驱动/服务器/认证信息：{'是（不合格）' if leak else '否'}")
    w("")
    if real_rows:
        w("【4】真实启动（python run.py，非测试客户端）")
        w("-" * 90)
        for label, expect, actual, status, note in real_rows:
            w(f"  {label:<24}{expect:>6}{actual:>6}  {status}  {note}")
        w("")
    w("【5】数据库错误分类（约束类不得被误判为无权限）")
    w("-" * 90)
    w(f"{'用例':<34}{'预期错误码':>12}{'实际':>10}  结果")
    for label, expect, actual, status in classify_rows:
        w(f"{label:<34}{expect:>12}{actual:>10}  {status}")
    w("")
    ok = (
        all(r[3] == "PASS" for r in rows)
        and not leak
        and all(r[3] == "PASS" for r in real_rows)
        and all(r[3] == "PASS" for r in classify_rows)
    )
    w("=" * 90)
    w("结论：" + ("全部通过" if ok else "存在失败项，需修复"))
    w("=" * 90)
    return "\n".join(lines) + "\n", ok


def main() -> int:
    write = "--write" in sys.argv
    real = "--real-server" in sys.argv

    rows, health, bad_resp, bad_body, leak = run_client_checks()
    real_rows = run_real_server() if real else []
    classify_rows = classify_checks()
    text, ok = render(rows, health, bad_resp, bad_body, leak, real_rows, classify_rows, Config)

    for label, expect, actual, status, note in rows + real_rows:
        print(f"[{status}] {label}: {actual} (expect {expect}) {note}")
    for label, expect, actual, status in classify_rows:
        print(f"[{status}] {label}: {actual} (expect {expect})")
    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")

    if write:
        name = f"第7周-①②-骨架自检-{dt.date.today():%Y%m%d}.txt"
        path = os.path.join(ROOT, "result", name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("evidence:", os.path.relpath(path, ROOT))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
