"""⑦ 集成测试与异常验证：按 docs/09-应用开发/测试用例.md 的 A–J 组逐条执行。

这是 ⑦ 的**总回填入口**：130 条用例按 ID 逐条断言，一条一行，
产出 `result/第8周-⑦集成测试-<日期>.txt`（用例 ID | 预期 | 实际 | 结果 | 备注）。

分工口径（为什么长这样）：

  · **A–H 组**跑在 `MilkTeaShop_v01` 上，用「外层事务 + SAVEPOINT」整轮回滚
    （沿用 ⑤ `order_pages_test.py` 的 harness），跑完 seed 数据与行数不变；
  · **I 组（并发/幂等/故障注入）**必须真提交才测得出竞态，跑在 `MilkTeaShop_test`
    沙箱（由 `scripts/create_test_db.sql` 从 v01 复制而来，源库只读）；
  · **J 组（复现与交付）**与外部环境有关：REPRO-01 会**删库重建 v0.1**
    （`sql/00-create.sql` 先删后建），故默认不跑、只在 `--repro` 时执行并自带备份兜底；
    REPRO-02 沿用第 6 周证据 + 只读现状核对；REPRO-03/04 在 `--real-server` 时真起服务。

用法（仓库根目录）：

    python scripts/integration_test.py                 # 只跑并打印（不碰 v0.1、不建测试库）
    python scripts/integration_test.py --write          # 另写 result/ 证据
    python scripts/integration_test.py --sql-auth       # DB_AUTH=sql（5 个受限登录）
    python scripts/integration_test.py --concurrency    # 建测试库并跑 I 组
    python scripts/integration_test.py --real-server    # 真起 HTTP 服务跑 REPRO-03/04
    python scripts/integration_test.py --repro          # 真跑 REPRO-01（删库重建 v0.1，带备份兜底）

退出码：0 = 无 FAIL；1 = 有 FAIL（SKIP / REF 不算失败，但会在总计里单列）。
"""
from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pyodbc

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app                                        # noqa: E402
from app.config import Config                                     # noqa: E402
from app.dao.connection import connect                             # noqa: E402
from app.errors import AppError, classify_db_error                 # noqa: E402
import app.services.inventory_service as inventory_svc             # noqa: E402
import app.services.order_service as order_svc                     # noqa: E402
import app.services.product_service as product_svc                 # noqa: E402

PASSWORD = {
    "clerk1": "clerk123",
    "customer1": "cust123",
    "maker1": "maker123",
    "stock1": "stock123",
    "manager1": "mgr123",
}

# --sql-auth 用 sql/app_login.sql 里的示例密码（可由同名环境变量覆盖）
_SQL_LOGIN_PWD = {
    "DB_PWD_LOGIN_CLERK": "Clerk#2026",
    "DB_PWD_LOGIN_CUSTOMER": "Cust#2026",
    "DB_PWD_LOGIN_MAKER": "Maker#2026",
    "DB_PWD_LOGIN_STOCK": "Stock#2026",
    "DB_PWD_LOGIN_MANAGER": "Mgr#2026",
    "DB_PWD_LOGIN_APP": "App#2026",
}
_SQL_ROLE_LOGIN = {
    "clerk": "login_clerk",
    "customer": "login_customer",
    "maker": "login_maker",
    "stock": "login_stock",
    "manager": "login_manager",
}

TEST_DB = "MilkTeaShop_test"
def _find_sqlcmd() -> Path:
    """优先 PATH 上的 sqlcmd，其次 18 版默认安装路径（换机器时不会静默降级成 SKIP）。"""
    found = shutil.which("sqlcmd")
    if found:
        return Path(found)
    return Path(r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\180\Tools\Binn\SQLCMD.EXE")


SQLCMD = _find_sqlcmd()

# 泄露自检：错误页/正常页都不该出现的实现细节
LEAK_TOKENS = ("SELECT ", "INSERT INTO", "dbo.", "pyodbc", "SQLEXPRESS", "Traceback", "ODBC Driver")


def leaks(body: str) -> bool:
    return any(token in body for token in LEAK_TOKENS)


def _d(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# 用例目录：ID → (优先级, 一句话说明)
# 与 docs/09-应用开发/测试用例.md §5 严格对应，共 130 条
# ---------------------------------------------------------------------------
CASES: dict[str, tuple[str, str]] = {
    # A. 认证与会话
    "AUTH-01": ("P0", "未登录访问登录页：200，含 5 个演示账号，不含密码"),
    "AUTH-02": ("P0", "正确凭据登录：302，session 写入 role/username"),
    "AUTH-03": ("P0", "密码错误：401 E2002，不建 session，不泄露用户是否存在"),
    "AUTH-04": ("P0", "缺用户名或密码：400 E1001"),
    "AUTH-05": ("P0", "用户名 31 字 / 密码 129 字：400 E1001"),
    "AUTH-06": ("P0", "退出后访问受保护页：401 E2001"),
    "AUTH-07": ("P0", "未登录访问商品/库存/订单：均 401 E2001"),
    "AUTH-08": ("P1", "不退出直接换账号：旧 session 清空，无残留 member_id"),
    "AUTH-09": ("P1", "篡改 session cookie 的 role：签名校验失败，不提权"),
    "AUTH-10": ("P1", "对 GET-only 路由发 POST：405 E3002"),
    # B. 权限与越权
    "PERM-01": ("P0", "匿名 GET /products/：401 E2001"),
    "PERM-02": ("P0", "顾客 GET /inventory/：403 E2003"),
    "PERM-03": ("P0", "顾客 GET /orders/：403 E2003"),
    "PERM-04": ("P0", "顾客 GET /orders/mine：200 且只含自己的单"),
    "PERM-05": ("P0", "制作员 GET /inventory/：403 E2003"),
    "PERM-06": ("P0", "库存管理员 GET /orders/：403 E2003"),
    "PERM-07": ("P0", "店员读商品/库存/订单：均 200"),
    "PERM-08": ("P0", "店长读商品/库存/订单：均 200"),
    "PERM-09": ("P0", "顾客 POST /products/：403，无新增行"),
    "PERM-10": ("P0", "店员 POST /inventory/adjust：403，库存与流水不变"),
    "PERM-11": ("P0", "制作员 POST /orders/<id>/refund：403，订单不变"),
    "PERM-12": ("P0", "店长 POST /inventory/adjust：403（无 inventory.adjust）"),
    "PERM-13": ("P0", "表单额外传 role=manager：仍按 session 角色判定"),
    "PERM-14": ("P0", "IDOR：顾客看自己的单 200、看别人的单 404"),
    "PERM-15": ("P1", "顾客页面不出现库存/退款/商品维护入口"),
    "PERM-16": ("P1", "sql 模式：顾客改 item_material.stock_qty → DB 230"),
    "PERM-17": ("P1", "sql 模式：店员写 shop_order.refund_amount → DB 230"),
    "PERM-18": ("P1", "sql 模式：店长读 item_material → 成功"),
    # C. 商品
    "PROD-01": ("P0", "店员 GET /products/：200，列出 5 个商品"),
    "PROD-02": ("P1", "按名称模糊搜索：只含匹配商品（参数化）"),
    "PROD-03": ("P0", "店长新增合法商品：成功且落库 1 行"),
    "PROD-04": ("P0", "新增重复 (name,size)：409 E5002，不产生重复行"),
    "PROD-05": ("P1", "新增非法杯型 HUGE：400 E1001，无脏数据"),
    "PROD-06": ("P1", "新增负价 unit_price=-1：400 E1001"),
    "PROD-07": ("P0", "店员 POST /products/：403，无新增行"),
    "PROD-08": ("P1", "店长改价：库内价格更新，历史订单 unit_price 快照不变"),
    "PROD-09": ("P1", "不存在的商品：404 E3001，不泄露 SQL"),
    "PROD-10": ("P1", "搜索注入 q=' OR 1=1 --：200，无 SQL 报错、不泄露全表异常"),
    "PROD-11": ("P1", "商品名含 <script>：落库成功但页面转义"),
    "PROD-12": ("P2", "名称空/全空格/超长：400 E1001"),
    # D. 库存与补货
    "INV-01": ("P0", "库存管理员 GET /inventory/：200，8 种原料，M0000008 标低库存"),
    "INV-02": ("P1", "店员 GET /inventory/：200 只读，无“调整库存”表单"),
    "INV-03": ("P0", "顾客 GET /inventory/：403 E2003"),
    "INV-04": ("P0", "调整 +10：stock+10 且写 GAIN 10 流水"),
    "INV-05": ("P0", "调整 -5：stock-5 且写 SHORT 5 流水"),
    "INV-06": ("P0", "库存为 0 时调整 -1：409，库存不变、无流水"),
    "INV-07": ("P0", "调整后 stock<reserved：409，无半条流水"),
    "INV-08": ("P0", "店员 POST /inventory/adjust：403，库存/流水不变"),
    "INV-09": ("P0", "店长 POST /inventory/adjust：403（店长无该权限）"),
    "INV-10": ("P0", "创建补货单：PENDING、received_at 为 NULL、不写库存流水"),
    "INV-11": ("P0", "验收补货单：RECEIVED、received_at 非空、库存增加、写 IN 流水并关联 restock_id"),
    "INV-12": ("P1", "重复验收：409，库存不二次增加、流水不重复"),
    "INV-13": ("P1", "验收不存在的补货单：404 E3001"),
    "INV-14": ("P1", "调整不存在的原料：404 E3001"),
    "INV-15": ("P2", "调整数量非数字/极大值：4xx，不写库、不 500"),
    # E. 订单与事务
    "ORD-01": ("P0", "店员 GET /orders/：200，7 张基线订单可见、分页正常"),
    "ORD-02": ("P0", "制作员 GET /orders/：200（制作队列）"),
    "ORD-03": ("P0", "顾客 GET /orders/：403 E2003"),
    "ORD-04": ("P0", "顾客 GET /orders/mine：只含 C0000001 的单"),
    "ORD-05": ("P0", "到店现金单：PAID、扣现存、写 SALE 流水、金额正确"),
    "ORD-06": ("P0", "自助线上单：PENDING、只预占不扣现存、不写流水"),
    "ORD-07": ("P0", "支付确认：PAID、预占释放、扣现存、写 SALE、积分 +12"),
    "ORD-08": ("P0", "平台单：PAID、不预占、order_platform 1 行、order_mode 为 NULL"),
    "ORD-09": ("P0", "顾客篡改 member_id：订单仍绑定 session 会员"),
    "ORD-10": ("P0", "订单头：单号 YYYYMMDD+4、employee_id 非空、金额等式成立"),
    "ORD-11": ("P0", "两行明细：item_line 1..2、unit_price 为下单时快照"),
    "ORD-12": ("P0", "加料快照：addon_price 取 item_addon_option，计入 item_subtotal"),
    "ORD-13": ("P0", "加料不属于该商品/非 ADDON：4xx，无残留数据"),
    "ORD-14": ("P0", "库存不足：409 E4001，无半条订单、预占不变"),
    "ORD-15": ("P1", "不存在的 product_id：4xx，无半条数据"),
    "ORD-16": ("P1", "qty=0 / qty=-1：400 E1001，无半条数据"),
    "ORD-17": ("P1", "已用/已过期/非本人的券：409 E4001，券状态不变"),
    "ORD-18": ("P1", "余额不足：409 E4001，余额不变、无流水"),
    "ORD-19": ("P1", "顾客伪造 channel=PLATFORM/employee_id/refund_amount：被忽略或 4xx"),
    "ORD-20": ("P1", "同日连续 2 单：单号递增且唯一"),
    "ORD-21": ("P1", "订单号碰撞：应用层重试后成功，无重复单号"),
    "ORD-22": ("P0", "取消 PENDING：CANCELLED、预占释放、不写库存流水、券退回"),
    "ORD-23": ("P0", "取消 PAID：409 E4001，状态不变"),
    "ORD-24": ("P0", "店长整单退款：REFUNDED、退余额/冲积分/退券、库存不回补"),
    "ORD-25": ("P0", "PENDING/CANCELLED 单退款：409 E4001"),
    "ORD-26": ("P0", "制作状态机 PAID→MAKING→COMPLETED，completed_at 写入"),
    "ORD-27": ("P0", "PENDING→COMPLETED：409 E4001"),
    "ORD-28": ("P0", "制作员改 total_amount/refund_amount：金额不变"),
    "ORD-29": ("P0", "店员退款：403 E2003"),
    "ORD-30": ("P1", "故障注入（明细写入抛错）：500，订单头也回滚、无半条数据"),
    "ORD-31": ("P1", "重复支付同一 pay_txn_no：幂等/409，库存与积分不重复变更"),
    "ORD-32": ("P1", "余额支付：不计积分，余额流水正确"),
    "ORD-33": ("P2", "同一单重复取消：409，预占不重复释放"),
    # F. 查询与统计
    "QRY-01": ("P1", "订单详情：订单头+明细+加料+流水关系正确"),
    "QRY-02": ("P1", "销量统计：与基线汇总一致、无重复计数"),
    "QRY-03": ("P1", "低库存筛选：含 M0000008、不含高于安全库存的原料"),
    "QRY-04": ("P1", "订单日期范围筛选：边界日期包含"),
    "QRY-05": ("P2", "分页边界（0/负数/超大）：4xx 或 200，不 500、不返回全表"),
    "QRY-06": ("P2", "查询参数注入 ' OR 1=1 --、%/_：200，参数化生效"),
    # G. 异常与安全
    "ERR-01": ("P1", "未知路径：404 E3001，错误页含 request_id、无堆栈"),
    "ERR-02": ("P1", "对 GET 路由发 POST：405 E3002"),
    "ERR-03": ("P0", "配置不存在的库：/health 503 E5001，不泄露库名/驱动/服务器"),
    "ERR-04": ("P1", "唯一键冲突：409 E5002（2627 不被误判为 403）"),
    "ERR-05": ("P1", "CHECK/FK 冲突：409 E5002（547 归类正确）"),
    "ERR-06": ("P1", "sql 模式受限账号越权写：229/230 → 403 E2003"),
    "ERR-07": ("P1", "注入未预期异常：500 E9001，堆栈只进日志"),
    "ERR-08": ("P1", "日志可追溯：含 rid/角色/错误码/路径，不含密码"),
    "ERR-09": ("P1", "错误响应体不含 SQL/表名/驱动/服务器/账号"),
    "ERR-10": ("P1", "登录表单注入 ' OR '1'='1：401 E2002，不能绕过"),
    "ERR-11": ("P1", "session cookie 安全属性：HttpOnly、SameSite=Lax"),
    "ERR-12": ("P1", "篡改 session cookie：退回匿名，不提权"),
    "ERR-13": ("P2", "商品名/备注含脚本：页面转义，不执行"),
    "ERR-14": ("P2", "超长/空/Unicode/emoji 输入：400 或正常处理，不 500"),
    # H. 数据库一致性
    "DB-01": ("P0", "结构指纹 236（111 列/17 PK/3 UQ/4 筛选唯一/25 FK/60 CHECK/16 DEFAULT）"),
    "DB-02": ("P0", "订单金额等式违例 0 条"),
    "DB-03": ("P0", "reserved_qty <= stock_qty 违例 0 条"),
    "DB-04": ("P0", "测试订单的明细+加料金额与 item_subtotal 一致"),
    "DB-05": ("P0", "测试订单的 SALE 流水数量与库存扣减一致"),
    "DB-06": ("P1", "会员积分/余额快照与流水一致"),
    "DB-07": ("P1", "外码/CHECK 约束无禁用项"),
    "DB-08": ("P1", "孤儿行 0 条（明细/加料均有父行）"),
    "DB-09": ("P1", "订单号与支付流水号无重复"),
    "DB-10": ("P2", "加料快照不随现价变化"),
    # I. 并发、幂等与故障注入（沙箱 MilkTeaShop_test，真提交）
    "CONC-01": ("P1", "末件库存并发下单：一成功一 409，不超卖"),
    "CONC-02": ("P1", "同一单并发/重复支付：只生效一次"),
    "CONC-03": ("P1", "同一 PENDING 单并发取消：只释放一次"),
    "CONC-04": ("P1", "同日并发下单：单号全唯一、无 2627 逃逸"),
    "CONC-05": ("P2", "并发库存调整 +10/-10：终值正确、流水与快照一致"),
    "CONC-06": ("P2", "事务中途抛异常：订单/明细/加料/流水/预占全部回滚"),
    # J. 复现与交付
    "REPRO-01": ("P0", "空库复现 v0.1：00→01→constraint→02→view→role 全部 exit=0"),
    "REPRO-02": ("P0", "迁移 + 回归：指纹 219→236、回归六段全过"),
    "REPRO-03": ("P0", "启动应用：/health 返回 total=236、match=true"),
    "REPRO-04": ("P0", "5 个演示账号逐一登录成功"),
    "REPRO-05": ("P1", "README 命令可照抄执行，不依赖手工改脚本"),
    "REPRO-06": ("P1", "证据脱敏：result/ 与 logs/ 无真实密码/连接串、.env 未入库"),
}

GROUP_ORDER = ["AUTH", "PERM", "PROD", "INV", "ORD", "QRY", "ERR", "DB", "CONC", "REPRO"]
GROUP_TITLE = {
    "AUTH": "A. 认证与会话",
    "PERM": "B. 权限与越权",
    "PROD": "C. 商品",
    "INV": "D. 库存与补货",
    "ORD": "E. 订单与事务",
    "QRY": "F. 查询与统计",
    "ERR": "G. 异常与安全",
    "DB": "H. 数据库一致性",
    "CONC": "I. 并发、幂等与故障注入（沙箱 %s，真提交）" % TEST_DB,
    "REPRO": "J. 复现与交付",
}


class Runner:
    """A–H 组：外层事务 + SAVEPOINT，整轮回滚；同时负责收集所有用例结果。"""

    def __init__(self) -> None:
        # sql 模式借 role_app（写事务账户）开外层连接；trusted 模式用当前 Windows 账户
        self.conn = connect("app" if Config.DB_AUTH == "sql" else None)
        self.cur = self.conn.cursor()
        self.cur.execute("SET XACT_ABORT OFF")     # 单条语句报错不 doom 外层事务
        self.cur.execute("SAVE TRANSACTION sp_outer")

        # 三个服务都换成"同一连接 + SAVEPOINT"，让页面层/业务层与测试看到同一事务
        self._orig = (order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor)
        runner = self

        @contextmanager
        def fake_db_cursor(role=None, commit=False):
            runner.cur.execute("SAVE TRANSACTION sp_call")
            try:
                yield runner.cur
            except BaseException as exc:
                try:
                    runner.cur.execute("ROLLBACK TRANSACTION sp_call")
                except pyodbc.Error:
                    pass
                # 真实 db_cursor 会把 pyodbc.Error 归类成 AppError；这里必须同样处理，
                # 否则 create_order 的"订单号碰撞重试"（只认 AppError）在测试里永不触发
                if isinstance(exc, pyodbc.Error):
                    raise classify_db_error(exc) from exc
                raise
            else:
                if not commit:
                    runner.cur.execute("ROLLBACK TRANSACTION sp_call")

        order_svc.db_cursor = fake_db_cursor
        product_svc.db_cursor = fake_db_cursor
        inventory_svc.db_cursor = fake_db_cursor

        self.app = create_app()
        self.app.config["SERVER_NAME"] = "localhost"
        self.client = self.app.test_client()
        self.rows: dict[str, tuple[str, str, str, str]] = {}

    # ---- 结果收集 ------------------------------------------------------
    def rec(self, cid: str, expect, actual, ok: bool, note: str = "") -> None:
        """登记结果。**同一 cid 多次登记时合并而不是覆盖**——否则 check_in() 记录的状态码
        会被随后的 check_truth() 悄悄丢掉（曾导致 15 条用例的断言只剩后半段）。"""
        assert cid in CASES, f"未登记的用例 ID：{cid}"
        prev = self.rows.get(cid)
        if prev is None:
            self.rows[cid] = (str(expect), str(actual), "PASS" if ok else "FAIL", note)
            return
        p_expect, p_actual, p_status, p_note = prev
        status = "FAIL" if (p_status == "FAIL" or not ok) else p_status
        actual_merged = p_actual if p_actual == str(actual) else f"{p_actual}；{actual}"
        expect_merged = p_expect if p_expect == str(expect) else f"{p_expect}；{expect}"
        note_merged = "；".join(x for x in (p_note, note) if x)
        self.rows[cid] = (expect_merged, actual_merged, status, note_merged)

    def rec_skip(self, cid: str, expect, actual, note: str) -> None:
        assert cid in CASES, cid
        self.rows[cid] = (str(expect), str(actual), "SKIP", note)

    def rec_ref(self, cid: str, expect, actual, note: str) -> None:
        assert cid in CASES, cid
        self.rows[cid] = (str(expect), str(actual), "REF", note)

    def check(self, cid: str, expect, actual, note: str = "") -> None:
        self.rec(cid, expect, actual, expect == actual, note)

    def check_truth(self, cid: str, cond: bool, expect, actual, note: str = "") -> None:
        self.rec(cid, expect, actual, cond, note)

    def check_in(self, cid: str, codes, resp, note: str = "") -> str:
        """状态码落在预期集合内即通过；返回响应体（HTML 文本）。"""
        body = resp.get_data(as_text=True)
        self.rec(cid, "/".join(str(c) for c in codes), resp.status_code,
                 resp.status_code in codes, note)
        return body

    # ---- HTTP ----------------------------------------------------------
    def login(self, username: str) -> None:
        resp = self.client.post("/login", data={"username": username, "password": PASSWORD[username]})
        assert resp.status_code == 302, (username, resp.status_code)

    def logout(self) -> None:
        self.client.get("/logout")

    def get(self, path: str):
        return self.client.get(path)

    def post(self, path: str, data: dict | None = None):
        return self.client.post(path, data=data or {})

    def as_user(self, username: str) -> None:
        self.logout()
        self.login(username)

    # ---- 数据库小工具 --------------------------------------------------
    def one(self, sql: str, *params):
        self.cur.execute(sql, *params)
        row = self.cur.fetchone()
        return row[0] if row else None

    def all(self, sql: str, *params):
        self.cur.execute(sql, *params)
        return self.cur.fetchall()

    def scalar_int(self, sql: str, *params) -> int:
        return int(self.one(sql, *params))

    def stock(self, material_id: str) -> Decimal:
        return _d(self.one("SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", material_id))

    def reserved(self, material_id: str) -> Decimal:
        return _d(self.one("SELECT reserved_qty FROM dbo.item_material WHERE material_id = ?", material_id))

    def order_row(self, order_id: str):
        self.cur.execute(
            "SELECT order_id, channel, order_status, member_id, employee_id, item_subtotal, "
            "coupon_discount, total_amount, pay_method, completed_at, refund_amount, order_mode "
            "FROM dbo.shop_order WHERE order_id = ?", order_id)
        return self.cur.fetchone()

    def order_count(self) -> int:
        return self.scalar_int("SELECT COUNT(*) FROM dbo.shop_order")

    def last_order_id(self):
        return self.one("SELECT MAX(order_id) FROM dbo.shop_order")

    def max_log_id(self) -> int:
        return self.scalar_int("SELECT ISNULL(MAX(stock_log_id), 0) FROM dbo.inv_stock_log")

    def reserved_total(self) -> Decimal:
        return _d(self.one("SELECT SUM(reserved_qty) FROM dbo.item_material"))

    def points(self, member_id: str = "C0000001") -> int:
        return self.scalar_int("SELECT points FROM dbo.member_member WHERE member_id = ?", member_id)

    def balance(self, member_id: str = "C0000001") -> Decimal:
        return _d(self.one("SELECT balance FROM dbo.member_member WHERE member_id = ?", member_id))

    def recipe_usage(self, product_id: str) -> dict[str, Decimal]:
        """该商品一杯的原料耗用（material_id → 用量）。"""
        rows = self.all(
            "SELECT material_id, qty FROM dbo.item_recipe WHERE product_id = ?", product_id)
        return {r[0]: _d(r[1]) for r in rows}

    def product_price(self, product_id: str) -> Decimal:
        return _d(self.one("SELECT unit_price FROM dbo.item_product WHERE product_id = ?", product_id))

    def addon_price(self, product_id: str, material_id: str) -> Decimal:
        """该商品该加料的单价（必须带 product_id：同一原料可能被多个商品当加料，价格不同）。"""
        return _d(self.one(
            "SELECT addon_price FROM dbo.item_addon_option WHERE product_id = ? AND material_id = ?",
            product_id, material_id))

    def coupon_id(self, member_id: str, status: str = "UNUSED", usable_today: bool = True):
        sql = ("SELECT TOP 1 mc.member_coupon_id FROM dbo.mkt_member_coupon mc "
               "JOIN dbo.mkt_coupon_rule r ON r.coupon_rule_id = mc.coupon_rule_id "
               "WHERE mc.member_id = ? AND mc.coupon_status = ?")
        if usable_today:
            sql += (" AND r.valid_from <= CAST(SYSDATETIME() AS date)"
                    " AND r.valid_to >= CAST(SYSDATETIME() AS date)")
        row = self.all(sql + " ORDER BY mc.member_coupon_id", member_id)
        return int(row[0][0]) if row else None

    def close(self) -> None:
        order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor = self._orig
        try:
            self.conn.rollback()
        finally:
            self.cur.close()
            self.conn.close()


def app_error_code(func, *args, **kwargs) -> tuple[str, int]:
    """调用业务函数并捕获 AppError，返回 (错误码, HTTP 状态)。"""
    try:
        func(*args, **kwargs)
    except AppError as exc:
        return exc.code, exc.status
    return "-", 0


# ---------------------------------------------------------------------------
# B 组补充：sql 模式的数据库层权限探针（只读/no-op 写，跑完回滚）
# ---------------------------------------------------------------------------
def _sql_login_connect(login: str):
    pwd = Config.password_for(login) or _SQL_LOGIN_PWD[f"DB_PWD_{login.upper()}"]
    cs = (f"DRIVER={{{Config.DB_DRIVER}}};SERVER={Config.DB_SERVER};DATABASE={Config.DB_NAME};"
          f"UID={login};PWD={pwd};Encrypt=yes;TrustServerCertificate=yes;")
    return pyodbc.connect(cs, autocommit=False, timeout=5)


def _native_code(exc: BaseException) -> str:
    match = re.search(r"\((\d{3,5})\)", str(exc))
    return match.group(1) if match else "?"


def sql_permission_probes(r: Runner) -> None:
    """PERM-16/17/18 + ERR-06：只在 sql 模式成立；no-op 写后回滚，不动数据。"""
    if Config.DB_AUTH != "sql":
        for cid, expect in (("PERM-16", "DB 230"), ("PERM-17", "DB 230"),
                            ("PERM-18", "ok"), ("ERR-06", "E2003/403")):
            r.rec_skip(cid, expect, "未执行",
                       "DB_AUTH=trusted 下不适用（Windows 账户为 sysadmin），改用 --sql-auth 跑")
        return

    cases = [
        ("PERM-16", "customer", "UPDATE dbo.item_material SET stock_qty = stock_qty WHERE material_id = 'M0000001'", "230"),
        ("PERM-17", "clerk", "UPDATE dbo.shop_order SET refund_amount = refund_amount WHERE order_id = '202609160002'", "230"),
        ("PERM-18", "manager", "SELECT COUNT(*) FROM dbo.item_material", "ok"),
    ]
    for cid, role, sql, expect in cases:
        login = _SQL_ROLE_LOGIN[role]
        conn = None
        try:
            conn = _sql_login_connect(login)
            cur = conn.cursor()
            cur.execute(sql)
            cur.fetchall() if cur.description else None
            conn.rollback()
            actual = "ok"
        except pyodbc.Error as exc:
            actual = _native_code(exc)
            if conn is not None:
                try:
                    conn.rollback()
                except pyodbc.Error:
                    pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except pyodbc.Error:
                    pass
        r.check(cid, expect, actual, f"{login}")

    # ERR-06：同一类越权在应用层要归类为 403 E2003，而不是 500
    try:
        conn = _sql_login_connect(_SQL_ROLE_LOGIN["customer"])
        try:
            conn.cursor().execute(
                "UPDATE dbo.item_material SET stock_qty = stock_qty WHERE material_id = 'M0000001'")
        finally:
            conn.rollback()
            conn.close()
    except pyodbc.Error as exc:
        err = classify_db_error(exc)
        r.check("ERR-06", "E2003/403", f"{err.code}/{err.status}", f"native={_native_code(exc)}")
    else:
        r.rec("ERR-06", "E2003/403", "ok（未越权，异常）", False, "顾客竟然能改 stock_qty")



# ---------------------------------------------------------------------------
# A 组：认证与会话
# ---------------------------------------------------------------------------
def group_auth(r: Runner) -> None:
    # AUTH-01 未登录访问登录页
    body = r.check_in("AUTH-01", [200], r.get("/login"), "GET /login")
    accounts = ["clerk1", "customer1", "maker1", "stock1", "manager1"]
    has_all = all(u in body for u in accounts)
    leaks_pwd = any(p in body for p in PASSWORD.values())
    r.check_truth("AUTH-01", has_all and not leaks_pwd,
                  "5 账号/无明文密码", f"账号齐={has_all} 泄露密码={leaks_pwd}")

    # AUTH-02 正确凭据
    resp = r.post("/login", {"username": "clerk1", "password": "clerk123"})
    with r.client.session_transaction() as sess:
        role, user = sess.get("role"), sess.get("username")
    r.check("AUTH-02", "302/clerk/clerk1", f"{resp.status_code}/{role}/{user}", "session 已写入")
    r.logout()

    # AUTH-03 密码错误：与"用户不存在"的响应必须一致（不泄露账号是否存在）
    bad = r.post("/login", {"username": "clerk1", "password": "wrong-password"})
    with r.client.session_transaction() as sess:
        role_after_bad = sess.get("role")
    unknown = r.post("/login", {"username": "no-such-user", "password": "whatever"})

    def _msg(resp) -> str:
        # 错误页里 request_id 每次都不同，不能整页比对；只取提示文案 + 错误码
        text = resp.get_data(as_text=True)
        head = re.search(r"<h1>(.*?)</h1>", text, re.S)
        code = re.search(r"<code>(E\d{4})</code>", text)
        return f"{head.group(1).strip() if head else ''}|{code.group(1) if code else ''}"

    same_msg = _msg(bad) == _msg(unknown)
    r.check_truth(
        "AUTH-03",
        bad.status_code == 401 and "E2002" in bad.get_data(as_text=True)
        and role_after_bad is None and same_msg,
        "401 E2002/无 session/文案一致",
        f"{bad.status_code} role={role_after_bad} 文案一致={same_msg}")

    # AUTH-04 缺字段
    miss1 = r.post("/login", {"username": "", "password": "x"})
    miss2 = r.post("/login", {"username": "clerk1", "password": ""})
    r.check_truth("AUTH-04",
                  miss1.status_code == 400 and miss2.status_code == 400
                  and "E1001" in miss1.get_data(as_text=True),
                  "400 E1001", f"{miss1.status_code}/{miss2.status_code}")

    # AUTH-05 超长用户名 / 密码
    long_user = r.post("/login", {"username": "u" * 31, "password": "x"})
    long_pwd = r.post("/login", {"username": "clerk1", "password": "p" * 129})
    r.check_truth("AUTH-05", long_user.status_code == 400 and long_pwd.status_code == 400,
                  "400/400", f"{long_user.status_code}/{long_pwd.status_code}")

    # AUTH-06 退出后访问受保护页
    r.login("clerk1")
    out = r.get("/logout")
    after = r.get("/products/")
    r.check_truth("AUTH-06", out.status_code == 302 and after.status_code == 401,
                  "302 后 401", f"logout={out.status_code} 之后={after.status_code}")

    # AUTH-07 未登录访问三个受保护页
    codes = [r.get(p).status_code for p in ("/products/", "/inventory/", "/orders/")]
    r.check_truth("AUTH-07", codes == [401, 401, 401], "401/401/401",
                  "/".join(str(c) for c in codes))

    # AUTH-08 不退出直接换账号
    r.login("customer1")
    with r.client.session_transaction() as sess:
        member_before = sess.get("member_id")
    switch = r.post("/login", {"username": "clerk1", "password": "clerk123"})
    with r.client.session_transaction() as sess:
        role_now, member_now = sess.get("role"), sess.get("member_id")
    r.check_truth("AUTH-08",
                  switch.status_code == 302 and role_now == "clerk" and member_now is None,
                  "302/clerk/无 member_id",
                  f"{switch.status_code}/{role_now} member={member_now}（原 {member_before}）")

    # AUTH-09 / ERR-12 篡改 session cookie（签名失效 → 退回匿名，不提权）
    # 用"新客户端 + 显式 Cookie 头"发送被篡改的值：比改本客户端 cookie 更确定，
    # 否则旧 cookie 可能仍被带上，出现"改了半天其实没生效"的假 PASS/假 FAIL。
    cookie = r.client.get_cookie("session")
    raw = cookie.value if cookie else ""
    mid = len(raw) // 2
    flipped = "A" if raw[mid] != "A" else "B"
    tampered = raw[:mid] + flipped + raw[mid + 1:]
    forged_client = r.app.test_client()
    forged = forged_client.get("/products/", headers={"Cookie": f"session={tampered}"})
    forged_admin = forged_client.get("/orders/", headers={"Cookie": f"session={tampered}"})
    r.check_truth("AUTH-09", forged.status_code == 401 and forged_admin.status_code == 401,
                  "401/401（签名失效即匿名）",
                  f"篡改后 /products/={forged.status_code} /orders/={forged_admin.status_code}")
    r.check_truth("ERR-12", forged.status_code == 401,
                  "401/不提权", f"篡改后 {forged.status_code}")

    # AUTH-10 对 GET-only 路由发 POST
    r.as_user("clerk1")
    r.check("AUTH-10", "405", str(r.post("/logout").status_code))
    r.logout()


# ---------------------------------------------------------------------------
# B 组：权限与越权
# ---------------------------------------------------------------------------
def group_perm(r: Runner) -> None:
    # PERM-01 匿名
    r.check("PERM-01", "401", str(r.get("/products/").status_code))

    # PERM-02/03/04 顾客
    r.as_user("customer1")
    r.check("PERM-02", "403", str(r.get("/inventory/").status_code))
    r.check("PERM-03", "403", str(r.get("/orders/").status_code))
    mine = r.check_in("PERM-04", [200], r.get("/orders/mine"), "GET /orders/mine")
    own = [x[0] for x in r.all("SELECT order_id FROM dbo.shop_order WHERE member_id = 'C0000001'")]
    others = [x[0] for x in r.all(
        "SELECT order_id FROM dbo.shop_order WHERE member_id IS NOT NULL AND member_id <> 'C0000001'")]
    r.check_truth("PERM-04",
                  all(o in mine for o in own) and not any(o in mine for o in others),
                  "本人单齐全/他人单不出现",
                  f"本人 {len(own)} 单齐={all(o in mine for o in own)}，他人 {len(others)} 单泄露={any(o in mine for o in others)}")

    # PERM-05/06 越权读
    r.as_user("maker1")
    r.check("PERM-05", "403", str(r.get("/inventory/").status_code))
    r.as_user("stock1")
    r.check("PERM-06", "403", str(r.get("/orders/").status_code))

    # PERM-07/08 读权限齐
    for cid, user in (("PERM-07", "clerk1"), ("PERM-08", "manager1")):
        r.as_user(user)
        codes = [r.get(p).status_code for p in ("/products/", "/inventory/", "/orders/")]
        r.check_truth(cid, codes == [200, 200, 200], "200/200/200",
                      "/".join(str(c) for c in codes))

    # PERM-09 顾客新增商品
    r.as_user("customer1")
    before = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    resp = r.post("/products/", {"name": "越权商品", "size": "SMALL", "unit_price": "9.00"})
    after = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    r.check_truth("PERM-09", resp.status_code == 403 and after == before,
                  "403/无新增", f"{resp.status_code} {before}→{after}")

    # PERM-10 店员调库存
    r.as_user("clerk1")
    stock_before, log_before = r.stock("M0000001"), r.max_log_id()
    resp = r.post("/inventory/adjust", {"material_id": "M0000001", "delta": "10"})
    r.check_truth("PERM-10",
                  resp.status_code == 403 and r.stock("M0000001") == stock_before
                  and r.max_log_id() == log_before,
                  "403/库存与流水不变",
                  f"{resp.status_code} 库存 {stock_before}→{r.stock('M0000001')}")

    # PERM-11 制作员退款
    r.as_user("maker1")
    target = r.one("SELECT TOP 1 order_id FROM dbo.shop_order WHERE order_status = 'PAID' ORDER BY order_id")
    target = target or "202609160002"
    row_before = r.order_row(target)
    resp = r.post(f"/orders/{target}/refund", {})
    row_after = r.order_row(target)
    r.check_truth("PERM-11", resp.status_code == 403 and row_before == row_after,
                  "403/订单不变", f"{resp.status_code} {target}")

    # PERM-12 店长调库存
    r.as_user("manager1")
    stock_before = r.stock("M0000002")
    resp = r.post("/inventory/adjust", {"material_id": "M0000002", "delta": "10"})
    r.check_truth("PERM-12", resp.status_code == 403 and r.stock("M0000002") == stock_before,
                  "403/库存不变", f"{resp.status_code}")

    # PERM-13 表单伪造 role（角色只从 session 取）
    r.as_user("customer1")
    resp = r.post("/orders/", {
        "channel": "COUNTER", "order_mode": "COUNTER_SELF", "pay_method": "ONLINE",
        "role": "manager", "member_id": "C0000002", "qty.P0000001": "1",
    })
    forged_order = r.last_order_id()
    forged_row = r.order_row(forged_order) if forged_order else None
    member_used = forged_row[3] if forged_row else None
    r.check_truth("PERM-13", resp.status_code == 302 and member_used == "C0000001",
                  "302/仍按 customer 判定", f"{resp.status_code} member={member_used}")

    # PERM-14 IDOR
    own_id = r.one("SELECT TOP 1 order_id FROM dbo.shop_order WHERE member_id = 'C0000001' ORDER BY order_id")
    other_id = r.one("SELECT TOP 1 order_id FROM dbo.shop_order WHERE member_id = 'C0000002' ORDER BY order_id")
    own_resp = r.get(f"/orders/{own_id}")
    other_resp = r.get(f"/orders/{other_id}")
    r.check_truth("PERM-14", own_resp.status_code == 200 and other_resp.status_code == 404,
                  "200/404", f"本人 {own_id}={own_resp.status_code} 他人 {other_id}={other_resp.status_code}")

    # PERM-15 顾客页面入口（隐藏按钮只是体验控制）
    home = r.get("/").get_data(as_text=True)
    r.check_truth("PERM-15",
                  "库存" not in home and "整单退款" not in home and "商品维护" not in home,
                  "无库存/退款/商品维护入口", "首页只渲染 商品/下单/我的订单")
    r.logout()


# ---------------------------------------------------------------------------
# C 组：商品
# ---------------------------------------------------------------------------
def group_product(r: Runner) -> None:
    # PROD-01 列表
    r.as_user("clerk1")
    body = r.check_in("PROD-01", [200], r.get("/products/"), "GET /products/")
    names = [x[0] for x in r.all("SELECT name FROM dbo.item_product ORDER BY product_id")]
    prices = [str(_d(x[0])) for x in r.all("SELECT unit_price FROM dbo.item_product")]
    r.check_truth("PROD-01",
                  len(names) == 5 and all(n in body for n in names)
                  and sum(1 for p in prices if p in body) >= 4,
                  "5 个商品且名称/价格可见",
                  f"库内 {len(names)} 个，名称齐全={all(n in body for n in names)}")

    # PROD-02 模糊搜索（参数化）
    body = r.check_in("PROD-02", [200], r.get("/products/?q=珍珠"), "q=珍珠")
    matched = [x[0] for x in r.all("SELECT name FROM dbo.item_product WHERE name LIKE ?", "%珍珠%")]
    r.check_truth("PROD-02", all(m in body for m in matched),
                  "只含匹配商品", f"库内匹配 {len(matched)} 个：{matched}")

    # PROD-03 新增合法商品
    r.as_user("manager1")
    before = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    resp = r.post("/products/", {"name": "⑦集成测试-芋圆奶茶", "size": "MEDIUM",
                                 "unit_price": "16.50", "sale_status": "ON_SALE"})
    after = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    new_row = r.all("SELECT name, size, unit_price FROM dbo.item_product WHERE name = ?",
                    "⑦集成测试-芋圆奶茶")
    r.check_truth("PROD-03",
                  resp.status_code == 302 and after == before + 1
                  and new_row and new_row[0][1] == "MEDIUM" and _d(new_row[0][2]) == _d("16.50"),
                  "302/新增 1 行且字段正确",
                  f"{resp.status_code} {before}→{after} {new_row}")

    # PROD-04 重复 (name,size)
    before = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    resp = r.post("/products/", {"name": "⑦集成测试-芋圆奶茶", "size": "MEDIUM", "unit_price": "16.50"})
    after = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    r.check_truth("PROD-04", resp.status_code == 409 and "E5002" in resp.get_data(as_text=True)
                  and after == before,
                  "409 E5002/无重复行", f"{resp.status_code} {before}→{after}")

    # PROD-05 非法杯型
    resp = r.post("/products/", {"name": "⑦集成测试-杯型非法", "size": "HUGE", "unit_price": "10.00"})
    r.check_truth("PROD-05", resp.status_code == 400 and "E1001" in resp.get_data(as_text=True),
                  "400 E1001", str(resp.status_code))

    # PROD-06 负价
    resp = r.post("/products/", {"name": "⑦集成测试-负价", "size": "SMALL", "unit_price": "-1"})
    r.check_truth("PROD-06", resp.status_code == 400 and "E1001" in resp.get_data(as_text=True),
                  "400 E1001", str(resp.status_code))

    # PROD-07 店员新增
    r.as_user("clerk1")
    before = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    resp = r.post("/products/", {"name": "⑦集成测试-店员越权", "size": "SMALL", "unit_price": "9.00"})
    after = r.scalar_int("SELECT COUNT(*) FROM dbo.item_product")
    r.check_truth("PROD-07", resp.status_code == 403 and after == before,
                  "403/无新增", f"{resp.status_code} {before}→{after}")

    # PROD-08 改价不影响历史快照
    r.as_user("manager1")
    old_price = r.product_price("P0000001")
    snapshot_before = r.all(
        "SELECT order_id, item_line, unit_price FROM dbo.order_item WHERE product_id = 'P0000001' "
        "ORDER BY order_id, item_line")
    # 只改价：name/size 必须沿用当前值，否则会撞 (name,size) 唯一约束（那是 PROD-04 的场景）
    cur_name, cur_size, _cur_status = r.all(
        "SELECT name, size, sale_status FROM dbo.item_product WHERE product_id = 'P0000001'")[0]
    resp = r.post("/products/P0000001/edit",
                  {"name": cur_name, "size": cur_size, "unit_price": "12.50",
                   "sale_status": _cur_status})
    snapshot_after = r.all(
        "SELECT order_id, item_line, unit_price FROM dbo.order_item WHERE product_id = 'P0000001' "
        "ORDER BY order_id, item_line")
    r.check_truth("PROD-08",
                  resp.status_code == 302 and r.product_price("P0000001") == _d("12.50")
                  and snapshot_before == snapshot_after,
                  "302/现价更新、历史快照不变",
                  f"{old_price}→{r.product_price('P0000001')}，历史 {len(snapshot_before)} 行不变={snapshot_before == snapshot_after}")

    # PROD-09 不存在
    r.as_user("clerk1")
    body = r.check_in("PROD-09", [404], r.get("/products/P9999999"), "GET /products/P9999999")
    r.check_truth("PROD-09", "E3001" in body and not leaks(body),
                  "404 E3001/无泄露", f"E3001={'E3001' in body} 泄露={leaks(body)}")

    # PROD-10 搜索注入：参数化生效 → 注入串不命中任何商品（0 行），且不能变成 500
    resp = r.get("/products/?q=' OR 1=1 --")
    body = resp.get_data(as_text=True)
    shown = sum(1 for n in [x[0] for x in r.all("SELECT name FROM dbo.item_product")] if n in body)
    r.check_truth("PROD-10",
                  resp.status_code == 200 and shown == 0
                  and "E500" not in body and "E9001" not in body and not leaks(body),
                  "200/0 行/无 SQL 报错/无泄露",
                  f"{resp.status_code} 展示 {shown} 行（注入串不应命中任何商品）")

    # PROD-11 XSS 转义
    r.as_user("manager1")
    xss_name = "<script>alert(1)</script>"
    resp = r.post("/products/", {"name": xss_name, "size": "SMALL", "unit_price": "9.90"})
    listing = r.get("/products/?q=script").get_data(as_text=True)
    r.check_truth("PROD-11",
                  resp.status_code == 302 and "&lt;script&gt;" in listing
                  and "<script>alert(1)</script>" not in listing,
                  "302/页面转义",
                  f"{resp.status_code} 转义={'&lt;script&gt;' in listing} 原样出现={'<script>alert(1)</script>' in listing}")

    # PROD-12 空/全空格/超长
    codes = []
    for name in ("", "   ", "茶" * 101):
        codes.append(r.post("/products/", {"name": name, "size": "SMALL", "unit_price": "9.00"}).status_code)
    r.check_truth("PROD-12", codes == [400, 400, 400], "400/400/400",
                  "/".join(str(c) for c in codes))


# ---------------------------------------------------------------------------
# D 组：库存与补货
# ---------------------------------------------------------------------------
def group_inventory(r: Runner) -> None:
    # INV-01 库存列表
    r.as_user("stock1")
    body = r.check_in("INV-01", [200], r.get("/inventory/"), "GET /inventory/")
    count = r.scalar_int("SELECT COUNT(*) FROM dbo.item_material")
    # 口径与 DAO/模板一致：is_low = stock_qty < safety_stock（不是可售量）
    low = [x[0] for x in r.all(
        "SELECT material_id FROM dbo.item_material WHERE stock_qty < safety_stock ORDER BY material_id")]
    # 页面里"仅低库存"筛选框恒在，不能用 "低库存" in body 断言；要数徽标
    badges = body.count('<span class="badge">低库存</span>')
    r.check_truth("INV-01",
                  count == 8 and badges == len(low) and "M0000008" in low,
                  "8 种原料/低库存徽标数 == 库内低库存数",
                  f"库内 {count} 种；低库存 {low}；页面徽标 {badges} 个")

    # INV-02 店员只读
    r.as_user("clerk1")
    body = r.check_in("INV-02", [200], r.get("/inventory/"), "店员 GET /inventory/")
    r.check_truth("INV-02", "调整库存" not in body and "调整" not in body,
                  "无“调整库存”表单", "页面仅展示列表与流水入口")

    # INV-03 顾客越权
    r.as_user("customer1")
    r.check("INV-03", "403", str(r.get("/inventory/").status_code))

    # INV-04 调整 +10（GAIN）
    r.as_user("stock1")
    before = r.stock("M0000001")
    log_before = r.max_log_id()
    resp = r.post("/inventory/adjust", {"material_id": "M0000001", "delta": "10"})
    new_stock = r.stock("M0000001")
    log_row = r.all("SELECT log_type, qty, employee_id FROM dbo.inv_stock_log "
                    "WHERE stock_log_id > ? ORDER BY stock_log_id", log_before)
    r.check_truth("INV-04",
                  resp.status_code == 302 and new_stock == before + 10 and log_row
                  and log_row[-1][0] == "GAIN" and _d(log_row[-1][1]) == _d("10")
                  and log_row[-1][2] is not None,
                  "302/stock+10/GAIN 10",
                  f"{before}→{new_stock} 流水={log_row}")

    # INV-05 调整 -5（SHORT）
    before = r.stock("M0000001")
    log_before = r.max_log_id()
    resp = r.post("/inventory/adjust", {"material_id": "M0000001", "delta": "-5"})
    new_stock = r.stock("M0000001")
    log_row = r.all("SELECT log_type, qty FROM dbo.inv_stock_log "
                    "WHERE stock_log_id > ? ORDER BY stock_log_id", log_before)
    r.check_truth("INV-05",
                  resp.status_code == 302 and new_stock == before - 5 and log_row
                  and log_row[-1][0] == "SHORT" and _d(log_row[-1][1]) == _d("5"),
                  "302/stock-5/SHORT 5", f"{before}→{new_stock} 流水={log_row}")

    # INV-06 库存为 0 时调 -1
    target = r.one("SELECT TOP 1 material_id FROM dbo.item_material WHERE reserved_qty = 0 ORDER BY material_id")
    old_stock = r.stock(target)
    r.cur.execute("UPDATE dbo.item_material SET stock_qty = 0 WHERE material_id = ?", target)
    log_before = r.max_log_id()
    try:
        resp = r.post("/inventory/adjust", {"material_id": target, "delta": "-1"})
        after = r.stock(target)
        r.check_truth("INV-06", resp.status_code == 409 and after == _d("0") and r.max_log_id() == log_before,
                      "409/库存不变/无流水", f"{target} {resp.status_code} stock={after}")
    finally:
        r.cur.execute("UPDATE dbo.item_material SET stock_qty = ? WHERE material_id = ?", old_stock, target)

    # INV-07 调整后 stock < reserved
    reserved = r.reserved("M0000001")
    old_stock = r.stock("M0000001")
    r.cur.execute("UPDATE dbo.item_material SET stock_qty = 100 WHERE material_id = 'M0000001'")
    delta = -(Decimal("100") - reserved + 1)      # 100 + delta = reserved - 1
    log_before = r.max_log_id()
    try:
        resp = r.post("/inventory/adjust", {"material_id": "M0000001", "delta": str(delta)})
        after = r.stock("M0000001")
        r.check_truth("INV-07",
                      resp.status_code == 409 and after == _d("100") and r.max_log_id() == log_before,
                      "409/不违反 reserved<=stock/无流水",
                      f"delta={delta} {resp.status_code} stock={after} reserved={reserved}")
    finally:
        r.cur.execute("UPDATE dbo.item_material SET stock_qty = ? WHERE material_id = 'M0000001'", old_stock)

    # INV-08 店员调整
    r.as_user("clerk1")
    before, log_before = r.stock("M0000003"), r.max_log_id()
    resp = r.post("/inventory/adjust", {"material_id": "M0000003", "delta": "10"})
    r.check_truth("INV-08",
                  resp.status_code == 403 and r.stock("M0000003") == before
                  and r.max_log_id() == log_before,
                  "403/库存与流水不变", f"{resp.status_code}")

    # INV-09 店长调整
    r.as_user("manager1")
    before = r.stock("M0000003")
    resp = r.post("/inventory/adjust", {"material_id": "M0000003", "delta": "10"})
    r.check_truth("INV-09", resp.status_code == 403 and r.stock("M0000003") == before,
                  "403/库存不变", f"{resp.status_code}")

    # INV-10 创建补货单
    r.as_user("stock1")
    log_before = r.max_log_id()
    resp = r.post("/inventory/restocks", {"supplier_name": "⑦集成测试供应商",
                                          "material_id": "M0000008", "qty": "100"})
    row = r.all("SELECT restock_id, restock_status, received_at FROM dbo.inv_restock "
                "WHERE supplier_name = ?", "⑦集成测试供应商")
    r.check_truth("INV-10",
                  resp.status_code == 302 and row and row[0][1] == "PENDING"
                  and row[0][2] is None and r.max_log_id() == log_before,
                  "302/PENDING/received_at=NULL/无流水",
                  f"{resp.status_code} {row}")

    # INV-11 验收补货单
    pending = r.one("SELECT TOP 1 restock_id FROM dbo.inv_restock WHERE restock_status = 'PENDING' ORDER BY restock_id")
    item = r.all("SELECT material_id, qty FROM dbo.inv_restock_item WHERE restock_id = ?", pending)
    material_id, qty = item[0][0], _d(item[0][1])
    stock_before = r.stock(material_id)
    log_before = r.max_log_id()
    resp = r.post(f"/inventory/restocks/{pending}/receive", {})
    status_now, received_at = r.all(
        "SELECT restock_status, received_at FROM dbo.inv_restock WHERE restock_id = ?", pending)[0]
    log_row = r.all("SELECT log_type, qty, restock_id FROM dbo.inv_stock_log "
                    "WHERE stock_log_id > ? ORDER BY stock_log_id", log_before)
    r.check_truth("INV-11",
                  resp.status_code == 302 and status_now == "RECEIVED" and received_at is not None
                  and r.stock(material_id) == stock_before + qty and log_row
                  and log_row[-1][0] == "IN" and log_row[-1][2] == pending,
                  "302/RECEIVED/库存增加/IN 流水关联 restock_id",
                  f"{pending} {material_id} {stock_before}→{r.stock(material_id)}（补 {qty}）流水={log_row}")

    # INV-12 重复验收
    stock_now, logs_now = r.stock(material_id), r.max_log_id()
    resp = r.post(f"/inventory/restocks/{pending}/receive", {})
    r.check_truth("INV-12",
                  resp.status_code == 409 and r.stock(material_id) == stock_now
                  and r.max_log_id() == logs_now,
                  "409/库存不二次增加/流水不重复",
                  f"{resp.status_code} stock={r.stock(material_id)}")

    # INV-13 验收不存在的补货单
    resp = r.post("/inventory/restocks/R9999999/receive", {})
    r.check_truth("INV-13", resp.status_code == 404 and "E3001" in resp.get_data(as_text=True),
                  "404 E3001", str(resp.status_code))

    # INV-14 调整不存在的原料
    log_before = r.max_log_id()
    resp = r.post("/inventory/adjust", {"material_id": "M9999999", "delta": "10"})
    r.check_truth("INV-14", resp.status_code == 404 and r.max_log_id() == log_before,
                  "404 E3001/无流水", str(resp.status_code))

    # INV-15 非数字/极大值
    log_before = r.max_log_id()
    bad = r.post("/inventory/adjust", {"material_id": "M0000001", "delta": "abc"})
    huge = r.post("/inventory/adjust", {"material_id": "M0000001", "delta": "1e30"})
    r.check_truth("INV-15",
                  400 <= bad.status_code < 500 and 400 <= huge.status_code < 500
                  and r.max_log_id() == log_before,
                  "4xx/4xx/无写库", f"{bad.status_code}/{huge.status_code}")
    r.logout()


# ---------------------------------------------------------------------------
# E 组：订单与事务（33 条）
# ---------------------------------------------------------------------------
_STOCK_ORDER = {
    "channel": "COUNTER", "order_mode": "COUNTER_CASHIER", "pay_method": "CASH",
    "qty.P0000003": "1", "sugar.P0000003": "HALF", "ice.P0000003": "LESS",
    "temp.P0000003": "HOT", "addon.P0000003.M0000004": "1",
}


class CaseFailure(RuntimeError):
    """用例前置失败：由分组级 try/except 兜住，把该组未登记用例记成 FAIL（而不是整轮崩掉）。"""


def _cash_order(r: Runner, form: dict | None = None, note: str = "") -> str:
    """以店员身份下一张现金单，返回新单号。

    **必须校验 POST 真的落了一单**：早期版本直接 return MAX(order_id)，下单失败时会拿上一张
    旧单冒充新单，下游断言就会对着错对象"通过"。
    """
    r.as_user("clerk1")
    before = r.order_count()
    resp = r.post("/orders/", form or dict(_STOCK_ORDER))
    if resp.status_code != 302 or r.order_count() != before + 1:
        raise CaseFailure(f"现金单未创建：status={resp.status_code} 订单数 {before}→{r.order_count()}")
    return r.last_order_id()


def _self_order(r: Runner, product_id: str = "P0000003", qty: str = "1") -> str:
    """以顾客身份下一张自助单（PENDING），返回新单号；未落单则抛 CaseFailure。"""
    r.as_user("customer1")
    before = r.order_count()
    resp = r.post("/orders/", {
        "channel": "COUNTER", "order_mode": "COUNTER_SELF", "pay_method": "ONLINE",
        f"qty.{product_id}": qty,
    })
    if resp.status_code != 302 or r.order_count() != before + 1:
        raise CaseFailure(f"自助单未创建：status={resp.status_code} 订单数 {before}→{r.order_count()}")
    return r.last_order_id()


def group_order(r: Runner) -> None:
    baseline = ["202609160001", "202609160002", "202609160003", "202609160004",
                "202609160005", "202609160006", "202609160007"]

    # ORD-01 店员看全量 + 分页
    r.as_user("clerk1")
    body = r.check_in("ORD-01", [200], r.get("/orders/"), "GET /orders/")
    page = r.get("/orders/?limit=2").get_data(as_text=True)
    links = page.count('href="/orders/2026')
    r.check_truth("ORD-01",
                  all(o in body for o in baseline) and links == 2 and "offset=2" in page,
                  "7 张基线单可见/分页 2 条",
                  f"基线齐={all(o in body for o in baseline)} 每页 {links} 条")

    # ORD-02 制作员队列
    r.as_user("maker1")
    r.check("ORD-02", "200", str(r.get("/orders/").status_code))

    # ORD-03 顾客全量
    r.as_user("customer1")
    r.check("ORD-03", "403", str(r.get("/orders/").status_code))

    # ORD-04 我的订单
    body = r.check_in("ORD-04", [200], r.get("/orders/mine"), "GET /orders/mine")
    own = [x[0] for x in r.all("SELECT order_id FROM dbo.shop_order WHERE member_id = 'C0000001'")]
    other = [x[0] for x in r.all(
        "SELECT order_id FROM dbo.shop_order WHERE member_id IS NOT NULL AND member_id <> 'C0000001'")]
    r.check_truth("ORD-04",
                  all(o in body for o in own) and not any(o in body for o in other),
                  "只含 C0000001 的单",
                  f"本人 {len(own)} 单齐全={all(o in body for o in own)}，他人 {len(other)} 单泄露={any(o in body for o in other)}")

    # ORD-05 到店现金单
    usage = r.recipe_usage("P0000003")
    price = r.product_price("P0000003")
    addon_price = r.addon_price("P0000003", "M0000004")
    stock_before = {m: r.stock(m) for m in usage}
    log_before = r.max_log_id()
    cash_id = _cash_order(r)
    row = r.order_row(cash_id)
    expected_total = price + addon_price
    sale_rows = dict(r.all("SELECT material_id, SUM(qty) FROM dbo.inv_stock_log "
                           "WHERE order_id = ? AND log_type = 'SALE' GROUP BY material_id", cash_id))
    r.check_truth("ORD-05",
                  row is not None and row[2] == "PAID"
                  and _d(row[7]) == expected_total
                  and all(r.stock(m) == stock_before[m] - q for m, q in usage.items())
                  and all(_d(sale_rows.get(m, 0)) == q for m, q in usage.items()),
                  f"PAID/{expected_total}/扣料/SALE 流水与耗用一致",
                  f"{cash_id} 状态={row[2] if row else None} 金额={row[7] if row else None} SALE={ {m: str(v) for m, v in sale_rows.items()} }")

    # ORD-06 自助单：只预占
    stock_before = {m: r.stock(m) for m in usage}
    reserved_before = {m: r.reserved(m) for m in usage}
    log_before = r.max_log_id()
    self_id = _self_order(r)
    srow = r.order_row(self_id)
    r.check_truth("ORD-06",
                  srow is not None and srow[2] == "PENDING"
                  and all(r.stock(m) == stock_before[m] for m in usage)
                  and all(r.reserved(m) == reserved_before[m] + q for m, q in usage.items())
                  and r.max_log_id() == log_before,
                  "PENDING/仅预占/无流水",
                  f"{self_id} 状态={srow[2] if srow else None} 流水+{r.max_log_id() - log_before}")

    # ORD-07 支付确认
    points_before = r.points()
    reserved_before = {m: r.reserved(m) for m in usage}
    stock_before = {m: r.stock(m) for m in usage}
    log_before = r.max_log_id()
    resp = r.post(f"/orders/{self_id}/pay", {"pay_method": "ONLINE", "pay_txn_no": "⑦IT-ORD07"})
    prow = r.order_row(self_id)
    points_delta = r.points() - points_before
    expected_points = int(_d(prow[7])) if prow else 0
    r.check_truth("ORD-07",
                  resp.status_code == 302 and prow[2] == "PAID"
                  and all(r.reserved(m) == reserved_before[m] - q for m, q in usage.items())
                  and all(r.stock(m) == stock_before[m] - q for m, q in usage.items())
                  and r.max_log_id() > log_before and points_delta == expected_points,
                  f"PAID/预占释放/扣料/积分+{expected_points}",
                  f"{resp.status_code} 状态={prow[2]} 积分 {points_before}→{r.points()}")

    # ORD-08 平台单
    r.as_user("clerk1")
    usage2 = r.recipe_usage("P0000002")
    stock_before = {m: r.stock(m) for m in usage2}
    reserved_before = {m: r.reserved(m) for m in usage2}
    r.post("/orders/", {
        "channel": "PLATFORM", "platform_order_no": "⑦IT-MT-0001", "platform_name": "MEITUAN",
        "delivery_address": "中山大学东校园", "delivery_fee": "3.00", "commission_rate": "0.2000",
        "qty.P0000002": "1",
    })
    plat_id = r.last_order_id()
    plat = r.order_row(plat_id)
    plat_rows = r.scalar_int("SELECT COUNT(*) FROM dbo.order_platform WHERE order_id = ?", plat_id)
    plat_sale = r.scalar_int(
        "SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id = ? AND log_type = 'SALE'", plat_id)
    r.check_truth("ORD-08",
                  plat is not None and plat[2] == "PAID" and plat[11] is None and plat_rows == 1
                  and plat_sale > 0
                  and all(r.stock(m) == stock_before[m] - q for m, q in usage2.items())
                  and all(r.reserved(m) == reserved_before[m] for m in usage2),
                  "PAID/不预占/order_platform 1 行/order_mode=NULL/写 SALE",
                  f"{plat_id} 状态={plat[2] if plat else None} 平台行={plat_rows} "
                  f"order_mode={plat[11] if plat else None} SALE={plat_sale} 行")

    # ORD-09 篡改 member_id
    r.as_user("customer1")
    r.post("/orders/", {"channel": "COUNTER", "order_mode": "COUNTER_SELF", "pay_method": "ONLINE",
                        "member_id": "C0000002", "qty.P0000001": "1"})
    forged_id = r.last_order_id()
    r.check("ORD-09", "C0000001", r.order_row(forged_id)[3],
            f"{forged_id} 会员号只取 session")

    # ORD-10 订单头口径
    row = r.order_row(cash_id)
    items_sum = r.all("SELECT item_line, qty, unit_price FROM dbo.order_item WHERE order_id = ?", cash_id)
    addon_sum = r.all("SELECT qty, addon_price FROM dbo.order_item_addon WHERE order_id = ?", cash_id)
    calc_subtotal = sum(_d(i[2]) * int(i[1]) for i in items_sum) + sum(
        _d(a[1]) * int(a[0]) for a in addon_sum)
    equation = _d(row[7]) == _d(_d(row[5]) - _d(row[6]))
    r.check_truth("ORD-10",
                  re.fullmatch(r"\d{8}\d{4}", row[0]) is not None and row[4]
                  and _d(row[5]) == calc_subtotal and equation,
                  "单号 YYYYMMDD+4/employee_id 非空/金额等式成立",
                  f"{row[0]} employee={row[4]} subtotal={row[5]} 计算={calc_subtotal} 等式={equation}")

    # ORD-11 两行明细
    price1, price2 = r.product_price("P0000001"), r.product_price("P0000002")
    r.as_user("clerk1")
    r.post("/orders/", {"channel": "COUNTER", "order_mode": "COUNTER_CASHIER", "pay_method": "CASH",
                        "qty.P0000001": "1", "qty.P0000002": "2"})
    two_id = r.last_order_id()
    lines = r.all("SELECT item_line, product_id, qty, unit_price FROM dbo.order_item "
                  "WHERE order_id = ? ORDER BY item_line", two_id)
    r.check_truth("ORD-11",
                  [x[0] for x in lines] == [1, 2]
                  and [x[1] for x in lines] == ["P0000001", "P0000002"]
                  and _d(lines[0][3]) == price1 and _d(lines[1][3]) == price2
                  and int(lines[1][2]) == 2,
                  "item_line 1..2/价格快照/qty",
                  f"{two_id} 明细={[(x[0], x[1], x[3]) for x in lines]}")

    # ORD-12 加料快照与计费
    addon_row = r.all("SELECT material_id, qty, addon_price FROM dbo.order_item_addon "
                      "WHERE order_id = ?", cash_id)
    expected_subtotal = _d(price + addon_price)
    r.check_truth("ORD-12",
                  addon_row and _d(addon_row[0][2]) == addon_price
                  and _d(row[5]) == expected_subtotal,
                  f"addon_price={addon_price}/计入小计",
                  f"快照={addon_row[0][2] if addon_row else None} 小计={row[5]}")

    # ORD-13 加料不属于该商品（服务层校验，页面层会丢弃这类字段）
    before_count = r.order_count()
    code, status = app_error_code(
        order_svc.create_order,
        items=[{"product_id": "P0000001", "qty": 1,
                "addons": [{"material_id": "M0000006", "qty": 1}]}],
        role="clerk", employee_id="E0000001", channel="COUNTER",
        order_mode="COUNTER_CASHIER", pay_method="CASH")
    r.check_truth("ORD-13",
                  status in (400, 409) and r.order_count() == before_count,
                  "4xx/无残留", f"{code}/{status} 订单数不变={r.order_count() == before_count}")

    # ORD-14 库存不足
    mat = next(iter(r.recipe_usage("P0000003")))
    old_stock, old_reserved = r.stock(mat), r.reserved(mat)
    r.cur.execute("UPDATE dbo.item_material SET stock_qty = reserved_qty WHERE material_id = ?", mat)
    before_count = r.order_count()
    try:
        resp = r.post("/orders/", {"channel": "COUNTER", "order_mode": "COUNTER_SELF",
                                   "pay_method": "ONLINE", "qty.P0000003": "5"})
        r.check_truth("ORD-14",
                      resp.status_code == 409 and r.order_count() == before_count
                      and r.reserved(mat) == old_reserved,
                      "409 E4001/无半条订单/预占不变",
                      f"{resp.status_code} 订单数={r.order_count()} 预占={r.reserved(mat)}")
    finally:
        r.cur.execute("UPDATE dbo.item_material SET stock_qty = ? WHERE material_id = ?", old_stock, mat)

    # ORD-15 不存在的商品（页面层只遍历服务端商品，故走服务层）
    before_count = r.order_count()
    code, status = app_error_code(
        order_svc.create_order,
        items=[{"product_id": "P9999999", "qty": 1}],
        role="clerk", employee_id="E0000001", channel="COUNTER",
        order_mode="COUNTER_CASHIER", pay_method="CASH")
    r.check_truth("ORD-15", status in (400, 404) and r.order_count() == before_count,
                  "4xx/无半条数据", f"{code}/{status}")

    # ORD-16 qty 非法
    before_count = r.order_count()
    http_neg = r.post("/orders/", {"channel": "COUNTER", "order_mode": "COUNTER_CASHIER",
                                   "pay_method": "CASH", "qty.P0000003": "-1"})
    code, status = app_error_code(
        order_svc.create_order,
        items=[{"product_id": "P0000003", "qty": 0}],
        role="clerk", employee_id="E0000001", channel="COUNTER",
        order_mode="COUNTER_CASHIER", pay_method="CASH")
    r.check_truth("ORD-16",
                  http_neg.status_code == 400 and status == 400 and r.order_count() == before_count,
                  "400/400/无半条数据",
                  f"HTTP {http_neg.status_code}，服务层 {code}/{status}")

    # ORD-17 券不可用
    used_coupon = r.one("SELECT TOP 1 member_coupon_id FROM dbo.mkt_member_coupon "
                        "WHERE coupon_status = 'USED' ORDER BY member_coupon_id")
    other_coupon = r.one("SELECT TOP 1 mc.member_coupon_id FROM dbo.mkt_member_coupon mc "
                         "WHERE mc.member_id <> 'C0000001' ORDER BY mc.member_coupon_id")
    results = []
    coupon_ids = [c for c in (used_coupon, other_coupon) if c is not None]
    before_coupons = {c: r.all("SELECT coupon_status, used_order_id FROM dbo.mkt_member_coupon "
                               "WHERE member_coupon_id = ?", int(c))[0] for c in coupon_ids}
    for coupon in coupon_ids:
        results.append(app_error_code(
            order_svc.create_order,
            items=[{"product_id": "P0000003", "qty": 1}],
            role="customer", member_id="C0000001", channel="COUNTER",
            order_mode="COUNTER_SELF", pay_method="ONLINE", member_coupon_id=int(coupon)))
    after_coupons = {c: r.all("SELECT coupon_status, used_order_id FROM dbo.mkt_member_coupon "
                              "WHERE member_coupon_id = ?", int(c))[0] for c in coupon_ids}
    # 口径：seed 内没有"今天已过期"的券，故"过期券"子场景未覆盖（如实写在备注里）
    ok17 = (bool(results) and all(status in (400, 409) for _, status in results)
            and before_coupons == after_coupons)
    r.check_truth("ORD-17", ok17, "409 E4001/券状态不变",
                  f"已用券={used_coupon} 他人券={other_coupon} → {results}；券状态不变={before_coupons == after_coupons}"
                  "（无过期券样本，过期子场景未覆盖）")

    # ORD-18 余额不足
    balance = r.balance()
    log_before = r.scalar_int("SELECT COUNT(*) FROM dbo.member_balance_log")
    big_id = _self_order(r, qty="99")
    code, status = app_error_code(order_svc.pay_order, big_id, pay_method="BALANCE",
                                  role="customer", member_id="C0000001")
    r.check_truth("ORD-18",
                  status == 409 and r.balance() == balance
                  and r.scalar_int("SELECT COUNT(*) FROM dbo.member_balance_log") == log_before,
                  "409 E4001/余额不变/无流水",
                  f"余额 {balance} 需付 {r.order_row(big_id)[7]} → {code}/{status}")

    # ORD-19 顾客伪造平台单/经办人/退款额
    before_count = r.order_count()
    resp = r.post("/orders/", {"channel": "PLATFORM", "platform_order_no": "⑦IT-FAKE",
                               "platform_name": "MEITUAN", "delivery_address": "伪造",
                               "employee_id": "E0000001", "refund_amount": "99.00",
                               "qty.P0000003": "1"})
    r.check_truth("ORD-19", resp.status_code == 403 and r.order_count() == before_count,
                  "403/无伪造单", f"{resp.status_code}")
    # 顾客自助单的经办人由服务端取默认值（order_service.DEFAULT_EMPLOYEE_ID，docs/02 §5.7 #1），
    # 关键性质是"前端传 employee_id 改不动它"，不是"为 NULL"
    r.as_user("customer1")
    r.post("/orders/", {"channel": "COUNTER", "order_mode": "COUNTER_SELF", "pay_method": "ONLINE",
                        "employee_id": "E0000009", "refund_amount": "99.00", "qty.P0000001": "1"})
    forged2 = r.last_order_id()
    row2 = r.order_row(forged2)
    r.check_truth("ORD-19", row2[4] == order_svc.DEFAULT_EMPLOYEE_ID and row2[10] is None,
                  f"employee_id 固定为 {order_svc.DEFAULT_EMPLOYEE_ID}/refund_amount 为 NULL",
                  f"{forged2} employee={row2[4]} refund={row2[10]}")

    # ORD-20 同日两单
    id1 = _cash_order(r)
    id2 = _cash_order(r)
    r.check_truth("ORD-20", id1 and id2 and id1 != id2 and id2 > id1,
                  "单号递增且唯一", f"{id1} → {id2}")

    # ORD-21 订单号碰撞重试
    real_next = order_svc._order.next_order_id
    collided = "202609160001"
    state = {"n": 0}

    def flaky_next(cur, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            return collided
        return real_next(cur, **kwargs)

    order_svc._order.next_order_id = flaky_next
    try:
        retry_id = _cash_order(r)
    finally:
        order_svc._order.next_order_id = real_next
    r.check_truth("ORD-21", retry_id is not None and retry_id != collided and state["n"] >= 2,
                  "重试后成功且单号不重复",
                  f"尝试 {state['n']} 次 → {retry_id}")

    # ORD-22 取消 PENDING
    cancel_id = _self_order(r)
    r.as_user("clerk1")
    usage3 = r.recipe_usage("P0000003")
    reserved_before = {m: r.reserved(m) for m in usage3}
    log_before = r.max_log_id()
    points_before = r.points()
    resp = r.post(f"/orders/{cancel_id}/cancel", {})
    r.check_truth("ORD-22",
                  resp.status_code == 302 and r.order_row(cancel_id)[2] == "CANCELLED"
                  and all(r.reserved(m) == reserved_before[m] - q for m, q in usage3.items())
                  and r.max_log_id() == log_before and r.points() == points_before,
                  "CANCELLED/预占释放/不写库存流水/积分不变",
                  f"{resp.status_code} 预占={ {m: str(r.reserved(m)) for m in usage3} }")

    # ORD-23 取消 PAID
    before_status = r.order_row(cash_id)[2]
    resp = r.post(f"/orders/{cash_id}/cancel", {})
    r.check_truth("ORD-23", resp.status_code == 409 and r.order_row(cash_id)[2] == before_status,
                  "409 E4001/状态不变", f"{resp.status_code} {before_status}")

    # ORD-24 店长整单退款（现金单：不回补库存、不新增 SALE 流水）
    r.as_user("manager1")
    stock_before = {m: r.stock(m) for m in r.recipe_usage("P0000003")}
    log_before = r.max_log_id()
    resp = r.post(f"/orders/{cash_id}/refund", {})
    refunded = r.order_row(cash_id)
    r.check_truth("ORD-24",
                  resp.status_code == 302 and refunded[2] == "REFUNDED"
                  and _d(refunded[10]) == _d(refunded[7])
                  and all(r.stock(m) == stock_before[m] for m in stock_before)
                  and r.max_log_id() == log_before,
                  "REFUNDED/退款额=应付/库存不回补/无新流水",
                  f"{resp.status_code} 状态={refunded[2]} 退款额={refunded[10]}")

    # ORD-25 PENDING / CANCELLED 退款
    pending_id = _self_order(r)
    r.as_user("manager1")
    res_pending = r.post(f"/orders/{pending_id}/refund", {}).status_code
    res_cancelled = r.post(f"/orders/{cancel_id}/refund", {}).status_code
    r.check_truth("ORD-25", res_pending == 409 and res_cancelled == 409,
                  "409/409", f"{res_pending}/{res_cancelled}")

    # ORD-26 制作状态机
    r.as_user("maker1")
    first = r.post(f"/orders/{two_id}/status", {"to_status": "MAKING"})
    second = r.post(f"/orders/{two_id}/status", {"to_status": "COMPLETED"})
    done = r.order_row(two_id)
    r.check_truth("ORD-26",
                  first.status_code == 302 and second.status_code == 302
                  and done[2] == "COMPLETED" and done[9] is not None,
                  "302/302/COMPLETED/completed_at 已写",
                  f"{first.status_code}/{second.status_code} 状态={done[2]} completed_at={done[9]}")

    # ORD-27 PENDING → COMPLETED 非法
    pending2 = _self_order(r)
    r.as_user("maker1")
    resp = r.post(f"/orders/{pending2}/status", {"to_status": "COMPLETED"})
    r.check_truth("ORD-27", resp.status_code == 409 and r.order_row(pending2)[2] == "PENDING",
                  "409/状态不变", f"{resp.status_code}")

    # ORD-28 制作员改不动金额（**必须用能合法过渡的 PAID 单**：先前用 PENDING 单会被状态机
    # 直接 409 拒掉，金额当然不变——那是永真断言，等于没测）
    amount_target = _cash_order(r)
    r.as_user("maker1")
    before_amounts = (r.order_row(amount_target)[5], r.order_row(amount_target)[7],
                      r.order_row(amount_target)[10])
    resp = r.post(f"/orders/{amount_target}/status",
                  {"to_status": "MAKING", "total_amount": "0.01", "refund_amount": "99.00"})
    after_row = r.order_row(amount_target)
    after_amounts = (after_row[5], after_row[7], after_row[10])
    r.check_truth("ORD-28",
                  resp.status_code == 302 and after_row[2] == "MAKING"
                  and before_amounts == after_amounts,
                  "302（合法过渡发生）/金额不变",
                  f"{amount_target} 状态={after_row[2]} 金额 {before_amounts}→{after_amounts}")

    # ORD-29 店员退款
    r.as_user("clerk1")
    resp = r.post(f"/orders/{cash_id}/refund", {})
    r.check_truth("ORD-29", resp.status_code == 403, "403 E2003", str(resp.status_code))

    # ORD-30 故障注入：明细写入抛错 → 订单头也回滚
    before_count = r.order_count()
    before_logs = r.max_log_id()
    reserved_before = r.reserved_total()
    real_item = order_svc._order.insert_order_item

    def boom(*args, **kwargs):
        raise RuntimeError("⑦集成测试注入的异常")

    order_svc._order.insert_order_item = boom
    try:
        resp = r.post("/orders/", dict(_STOCK_ORDER))
    finally:
        order_svc._order.insert_order_item = real_item
    body = resp.get_data(as_text=True)
    r.check_truth("ORD-30",
                  resp.status_code == 500 and "E9001" in body
                  and r.order_count() == before_count and r.max_log_id() == before_logs
                  and r.reserved_total() == reserved_before,
                  "500 E9001/订单头也回滚/无残留",
                  f"{resp.status_code} 订单数 {before_count}→{r.order_count()} 预占差={reserved_before - r.reserved_total()}")

    # ORD-31 重复支付同一流水号
    dup_id = _self_order(r)
    points_before = r.points()
    stock_before = {m: r.stock(m) for m in r.recipe_usage("P0000003")}
    first_pay = r.post(f"/orders/{dup_id}/pay", {"pay_method": "ONLINE", "pay_txn_no": "⑦IT-DUP-1"})
    second_pay = r.post(f"/orders/{dup_id}/pay", {"pay_method": "ONLINE", "pay_txn_no": "⑦IT-DUP-1"})
    points_delta = r.points() - points_before
    expected_points = int(_d(r.order_row(dup_id)[7]))
    r.check_truth("ORD-31",
                  first_pay.status_code == 302 and second_pay.status_code in (302, 409)
                  and points_delta == expected_points
                  and all(r.stock(m) == stock_before[m] - q for m, q in r.recipe_usage("P0000003").items()),
                  f"302/{second_pay.status_code}/积分只变一次",
                  f"积分 +{points_delta}（应 +{expected_points}）")

    # ORD-32 余额支付不计积分
    bal_id = _self_order(r)
    balance_before, points_before = r.balance(), r.points()
    logs_before = r.scalar_int("SELECT COUNT(*) FROM dbo.member_balance_log")
    pay_total = _d(r.order_row(bal_id)[7])
    resp = r.post(f"/orders/{bal_id}/pay", {"pay_method": "BALANCE"})
    spend = r.all("SELECT balance_type, amount FROM dbo.member_balance_log "
                  "WHERE member_id = 'C0000001' ORDER BY balance_log_id DESC")
    r.check_truth("ORD-32",
                  resp.status_code == 302 and r.points() == points_before
                  and r.balance() == balance_before - pay_total
                  and r.scalar_int("SELECT COUNT(*) FROM dbo.member_balance_log") == logs_before + 1
                  and spend and spend[0][0] == "SPEND",
                  "302/不计积分/余额扣减/SPEND 流水",
                  f"余额 {balance_before}→{r.balance()} 积分不变={r.points() == points_before}")

    # ORD-33 重复取消
    dup_cancel = _self_order(r)
    r.as_user("clerk1")
    reserved_before = r.reserved_total()
    r.post(f"/orders/{dup_cancel}/cancel", {})
    reserved_after_first = r.reserved_total()
    second = r.post(f"/orders/{dup_cancel}/cancel", {})
    r.check_truth("ORD-33",
                  second.status_code == 409 and r.reserved_total() == reserved_after_first,
                  "409/预占不重复释放",
                  f"{second.status_code} 预占 {reserved_before}→{reserved_after_first}→{r.reserved_total()}")
    r.logout()


# ---------------------------------------------------------------------------
# F 组：查询与统计
# ---------------------------------------------------------------------------
def group_query(r: Runner) -> None:
    from app.dao import report as report_dao

    # QRY-01 订单详情：断言**页面渲染**了明细/加料/库存流水（店长才看得到流水段）
    r.as_user("manager1")
    target = r.one("SELECT TOP 1 order_id FROM dbo.order_item_addon ORDER BY order_id")
    resp = r.get(f"/orders/{target}")
    body = resp.get_data(as_text=True)
    items = r.all("SELECT product_id, size, qty FROM dbo.order_item WHERE order_id = ? "
                  "ORDER BY item_line", target)
    addon_names = [x[0] for x in r.all(
        "SELECT m.name FROM dbo.order_item_addon a JOIN dbo.item_material m ON m.material_id = a.material_id "
        "WHERE a.order_id = ?", target)]
    sale_logs = r.all("SELECT stock_log_id, material_id, qty FROM dbo.inv_stock_log "
                      "WHERE order_id = ? AND log_type = 'SALE' ORDER BY stock_log_id", target)
    r.check_truth("QRY-01",
                  resp.status_code == 200 and target in body
                  and all(pid in body and size in body for pid, size, _qty in items)
                  and all(n in body for n in addon_names if n)
                  and "本单库存流水（店长可见）" in body
                  and all(str(x[0]) in body for x in sale_logs),
                  "详情页渲染订单头+明细(商品号/杯型)+加料+库存流水",
                  f"{target} 明细 {len(items)} 行、加料 {len(addon_names)}、SALE 流水 {len(sale_logs)} 行")

    # QRY-02 销量统计：与**独立汇总**（order_item × 视图同口径状态）逐商品比对，而不是拿视图比自己
    summary = report_dao.product_sales_summary(r.cur, limit=200)
    expected = {x[0]: (int(x[1]), int(x[2])) for x in r.all(
        "SELECT i.product_id, SUM(i.qty), COUNT(DISTINCT i.order_id) FROM dbo.order_item i "
        "JOIN dbo.shop_order o ON o.order_id = i.order_id "
        "WHERE o.order_status IN ('PAID','MAKING','COMPLETED','REFUNDED') GROUP BY i.product_id")}
    got = {row["product_id"]: (int(row["sold_qty"]), int(row["order_count"])) for row in summary}
    r.check_truth("QRY-02", got == expected,
                  "视图 == 独立汇总（杯数与订单数逐商品一致）",
                  f"{len(got)} 个商品比对；差异={ {k: (got.get(k), expected.get(k)) for k in set(got) | set(expected) if got.get(k) != expected.get(k)} }")

    # QRY-03 低库存筛选
    r.as_user("stock1")
    low_body = r.check_in("QRY-03", [200], r.get("/inventory/?low_stock=1"), "low_stock=1")
    # 低库存口径与页面徽标一致：DAO 的 is_low = stock_qty < safety_stock（不是可售量）
    low_ids = [x[0] for x in r.all(
        "SELECT material_id FROM dbo.item_material WHERE stock_qty < safety_stock ORDER BY material_id")]
    # 页面同时渲染"调整库存"下拉（全量原料），故"筛选是否漏进高库存"必须用 DAO 口径断言
    from app.dao import inventory as inv_dao
    filtered = [row["material_id"] for row in inv_dao.list_materials(r.cur, low_stock=True)]
    all_ids = [x[0] for x in r.all("SELECT material_id FROM dbo.item_material")]
    high_ids = [m for m in all_ids if m not in low_ids]
    r.check_truth("QRY-03",
                  "M0000008" in low_body and filtered == low_ids
                  and not any(m in filtered for m in high_ids),
                  "页面含低库存标记 + 服务层筛选结果 == 低库存集合",
                  f"低库存 {low_ids}；高库存 {high_ids}；筛选返回 {filtered}")

    # QRY-04 日期范围边界
    r.as_user("clerk1")
    day = r.one("SELECT CAST(MIN(created_at) AS date) FROM dbo.shop_order")
    same_day = r.one(
        "SELECT TOP 1 order_id FROM dbo.shop_order WHERE CAST(created_at AS date) = ?", day)
    body = r.check_in("QRY-04", [200],
                      r.get(f"/orders/?date_from={day}&date_to={day}"), f"date_from=date_to={day}")
    r.check_truth("QRY-04", same_day in body, "边界日当天订单可见", f"{same_day}")

    # QRY-05 分页边界：`_int_arg` 的语义是"夹紧到 [low, high]"（不是报错），
    # 因此预期是 200 + 结果被限制，而不是 400（用例里写的是 "200/400"）
    codes = [r.get(f"/orders/?limit={v}").status_code for v in (0, -1, 999999)]
    zero_rows = r.get("/orders/?limit=0").get_data(as_text=True).count('href="/orders/2026')
    huge_rows = r.get("/orders/?limit=999999").get_data(as_text=True).count('href="/orders/2026')
    total_orders = r.order_count()
    r.check_truth("QRY-05",
                  codes == [200, 200, 200] and zero_rows <= 1 and huge_rows <= total_orders,
                  "200（夹紧）/limit=0 只出 ≤1 条/超大被夹到上限",
                  f"{codes} limit=0 → {zero_rows} 行；limit=999999 → {huge_rows} 行（共 {total_orders} 单）")

    # QRY-06 注入参数：注入串必须 0 命中（参数化生效），通配符 % / _ 按字面处理不报错
    inj = r.get("/orders/?q=%27%20OR%201%3D1%20--")
    inj_body = inj.get_data(as_text=True)
    orders_shown = sum(1 for o in [x[0] for x in r.all("SELECT order_id FROM dbo.shop_order")]
                       if o in inj_body)
    others = [r.get("/orders/?q=%25").status_code, r.get("/products/?q=%5F").status_code]
    r.check_truth("QRY-06",
                  inj.status_code == 200 and orders_shown == 0 and others == [200, 200]
                  and not leaks(inj_body) and "E500" not in inj_body,
                  "注入串 0 行/200/无泄露",
                  f"注入查询 {inj.status_code} 展示 {orders_shown} 单；%/ _ → {others}")
    r.logout()


# ---------------------------------------------------------------------------
# G 组：异常与安全
# ---------------------------------------------------------------------------
def group_error(r: Runner) -> None:
    # 记下日志文件当前长度：ERR-08 只核对**本轮新增**的那段，避免拿历史日志冒充
    log_path = ROOT / "logs" / "app.log"
    log_start = log_path.stat().st_size if log_path.exists() else 0

    # ERR-01 未知路径
    body = r.check_in("ERR-01", [404], r.get("/nope"), "GET /nope")
    r.check_truth("ERR-01",
                  "E3001" in body and re.search(r"请求标识：<code>[0-9a-f]{8,}</code>", body) is not None
                  and "Traceback" not in body,
                  "404 E3001/含 request_id/无堆栈",
                  f"rid={'有' if re.search(r'请求标识：<code>[0-9a-f]{8,}</code>', body) else '无'}")

    # ERR-02 GET 路由收到 POST
    r.check("ERR-02", "405", str(r.post("/health").status_code))

    # ERR-03 库不可用：503 且不泄露连接信息
    saved_db = Config.DB_NAME
    Config.DB_NAME = "⑦_不存在的库"
    try:
        broken_app = create_app()
        broken = broken_app.test_client()
        resp = broken.get("/health")
        body = resp.get_data(as_text=True)
    finally:
        Config.DB_NAME = saved_db
    r.check_truth("ERR-03",
                  resp.status_code == 503 and "E5001" in body and not leaks(body),
                  "503 E5001/无库名/驱动/服务器",
                  f"{resp.status_code} 泄露={leaks(body)}")

    # ERR-04 唯一键冲突（2627 不能被误判为 403）
    r.as_user("manager1")
    resp = r.post("/products/", {"name": "珍珠奶茶", "size": "LARGE", "unit_price": "12.50"})
    r.check_truth("ERR-04",
                  resp.status_code == 409 and "E5002" in resp.get_data(as_text=True),
                  "409 E5002", f"{resp.status_code}")

    # ERR-05 CHECK/FK 冲突 → E5002
    err = None
    r.cur.execute("SAVE TRANSACTION sp_err05")
    try:
        r.cur.execute(
            "INSERT INTO dbo.item_product (product_id, name, size, unit_price, sale_status) "
            "VALUES ('P⑦TEST', N'⑦CHECK 冲突', 'HUGE', 10.00, 'ON_SALE')")
    except pyodbc.Error as exc:
        err = classify_db_error(exc)
    finally:
        r.cur.execute("ROLLBACK TRANSACTION sp_err05")
    r.check_truth("ERR-05", err is not None and err.code == "E5002",
                  "E5002", f"{err.code if err else '未触发'}")

    # ERR-07 未预期异常 → 500 E9001
    real_list = product_svc.list_products

    def boom(*args, **kwargs):
        raise RuntimeError("⑦集成测试注入的未预期异常")

    product_svc.list_products = boom
    try:
        resp = r.get("/products/")
    finally:
        product_svc.list_products = real_list
    body = resp.get_data(as_text=True)
    r.check_truth("ERR-07",
                  resp.status_code == 500 and "E9001" in body
                  and "RuntimeError" not in body and "Traceback" not in body,
                  "500 E9001/堆栈只在日志",
                  f"{resp.status_code} 堆栈泄露={'Traceback' in body}")

    # ERR-08 日志可追溯：只读"本轮新增"的日志段，并核对 rid/角色/错误码/路径齐全
    with open(log_path, "rb") as fh:
        fh.seek(log_start)
        fresh = fh.read().decode("utf-8", errors="replace")
    has_rid = "rid=" in fresh
    has_role = re.search(r"role=(anonymous|clerk|customer|maker|stock|manager)", fresh) is not None
    has_code = any(code in fresh for code in ("E9001", "E5001", "E5002", "E3001", "E2003"))
    has_path = "/products/" in fresh or "/health" in fresh
    pwd_leak = any(p in fresh for p in list(PASSWORD.values()) + list(_SQL_LOGIN_PWD.values()))
    r.check_truth("ERR-08",
                  has_rid and has_role and has_code and has_path and not pwd_leak,
                  "本轮新增日志含 rid/角色/错误码/路径，不含密码",
                  f"新增 {len(fresh)} 字节：rid={has_rid} 角色={has_role} "
                  f"错误码={has_code} 路径={has_path} 密码泄露={pwd_leak}")

    # ERR-09 错误响应体不含敏感信息
    bodies = [
        r.get("/nope").get_data(as_text=True),
        r.get("/products/P9999999").get_data(as_text=True),
    ]
    r.check_truth("ERR-09", not any(leaks(b) for b in bodies),
                  "无 SQL/表名/驱动/服务器", f"{len(bodies)} 份错误页全部干净")

    # ERR-10 登录注入
    r.logout()
    resp = r.post("/login", {"username": "' OR '1'='1", "password": "' OR '1'='1"})
    with r.client.session_transaction() as sess:
        injected_role = sess.get("role")
    r.check_truth("ERR-10",
                  resp.status_code == 401 and "E2002" in resp.get_data(as_text=True)
                  and injected_role is None,
                  "401 E2002/未登录", f"{resp.status_code} role={injected_role}")

    # ERR-11 Set-Cookie 安全属性
    resp = r.post("/login", {"username": "clerk1", "password": "clerk123"})
    cookies = resp.headers.getlist("Set-Cookie")
    joined = " ".join(cookies)
    has_httponly = "HttpOnly" in joined
    has_samesite = "SameSite=Lax" in joined
    r.check_truth("ERR-11", has_httponly and has_samesite,
                  "HttpOnly/SameSite=Lax",
                  f"HttpOnly={has_httponly} SameSite=Lax={has_samesite}（不记录 cookie 取值）")

    # ERR-13 XSS 转义：**自带前置数据**（不能依赖 PROD 组先跑过，否则单跑 ERR 组会假红）
    r.as_user("manager1")
    r.post("/products/", {"name": "<script>alert(13)</script>", "size": "LARGE",
                          "unit_price": "9.80"})
    listing = r.get("/products/?q=script").get_data(as_text=True)
    r.check_truth("ERR-13",
                  "&lt;script&gt;" in listing and "<script>alert(13)</script>" not in listing,
                  "页面转义", "商品名中的 <script> 已转义为 &lt;script&gt;")

    # ERR-14 特殊字符输入不 500（用店长身份，否则全是 403，断言等于没测）
    r.as_user("manager1")
    codes = [
        r.post("/products/", {"name": "⑦emoji🧋奶茶", "size": "LARGE", "unit_price": "13.00"}).status_code,
        r.post("/products/", {"name": "茶" * 101, "size": "LARGE", "unit_price": "13.00"}).status_code,
        r.post("/products/", {"name": "   ", "size": "LARGE", "unit_price": "13.00"}).status_code,
    ]
    r.check_truth("ERR-14", all(c < 500 for c in codes), "无 500",
                  "/".join(str(c) for c in codes))
    r.logout()


# ---------------------------------------------------------------------------
# H 组：数据库一致性
# ---------------------------------------------------------------------------
def group_db(r: Runner) -> None:
    def count(sql: str, *params) -> int:
        return r.scalar_int(sql, *params)

    # DB-01 结构指纹
    columns = count("SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id")
    pk = count("SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK'")
    uq = count("SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ'")
    uq_f = count("SELECT COUNT(*) FROM sys.indexes i WHERE i.is_unique = 1 AND i.is_primary_key = 0 "
                 "AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0 "
                 "AND i.object_id IN (SELECT object_id FROM sys.tables)")
    fk = count("SELECT COUNT(*) FROM sys.foreign_keys")
    ck = count("SELECT COUNT(*) FROM sys.check_constraints")
    df = count("SELECT COUNT(*) FROM sys.default_constraints")
    total = columns + pk + uq + uq_f + fk + ck + df
    r.check_truth("DB-01", total == 236,
                  "236", f"列{columns}+PK{pk}+UQ{uq}+筛选唯一{uq_f}+FK{fk}+CK{ck}+DF{df}={total}")

    # DB-02 金额等式
    bad2 = count("""
        SELECT COUNT(*) FROM dbo.shop_order o
        WHERE o.total_amount <> o.item_subtotal - o.coupon_discount""")
    r.check_truth("DB-02", bad2 == 0, "0 违例", f"{bad2} 条违例")

    # DB-03 预占不超过现存
    bad3 = count("SELECT COUNT(*) FROM dbo.item_material WHERE reserved_qty > stock_qty")
    r.check_truth("DB-03", bad3 == 0, "0 违例", f"{bad3} 条违例")

    # DB-04 明细 + 加料金额与 item_subtotal 一致
    bad4 = count("""
        SELECT COUNT(*) FROM dbo.shop_order o
        WHERE ABS(o.item_subtotal - (
              ISNULL((SELECT SUM(i.unit_price * i.qty) FROM dbo.order_item i WHERE i.order_id = o.order_id), 0)
            + ISNULL((SELECT SUM(a.addon_price * a.qty) FROM dbo.order_item_addon a WHERE a.order_id = o.order_id), 0)
        )) > 0.005""")
    r.check_truth("DB-04", bad4 == 0, "0 违例", f"{bad4} 条违例（容差 0.005）")

    # DB-05 SALE 流水与库存扣减一致（双向：缺流水、多流水、数量不符都算违例）
    bad5 = count("""
        WITH exp AS (
            SELECT i.order_id, r.material_id, SUM(i.qty * r.qty) AS qty
            FROM dbo.order_item i JOIN dbo.item_recipe r ON r.product_id = i.product_id
            GROUP BY i.order_id, r.material_id
            UNION ALL
            SELECT a.order_id, a.material_id, SUM(a.qty * o.addon_qty)
            FROM dbo.order_item_addon a
            JOIN dbo.order_item i2 ON i2.order_id = a.order_id AND i2.item_line = a.item_line
            JOIN dbo.item_addon_option o ON o.product_id = i2.product_id AND o.material_id = a.material_id
            GROUP BY a.order_id, a.material_id
        ), expected AS (
            SELECT order_id, material_id, SUM(qty) AS qty FROM exp GROUP BY order_id, material_id
        ), sale AS (
            SELECT order_id, material_id, SUM(qty) AS qty FROM dbo.inv_stock_log
            WHERE log_type = 'SALE' GROUP BY order_id, material_id
        ), paid AS (
            SELECT order_id FROM dbo.shop_order
            WHERE order_status IN ('PAID', 'MAKING', 'COMPLETED', 'REFUNDED')
        )
        SELECT COUNT(*) FROM (
            SELECT e.order_id, e.material_id FROM expected e JOIN paid p ON p.order_id = e.order_id
            UNION
            SELECT s.order_id, s.material_id FROM sale s
        ) k
        LEFT JOIN expected e2 ON e2.order_id = k.order_id AND e2.material_id = k.material_id
        LEFT JOIN sale s2     ON s2.order_id = k.order_id AND s2.material_id = k.material_id
        WHERE e2.qty IS NULL OR s2.qty IS NULL OR ABS(e2.qty - s2.qty) > 0.005""")
    sale_orders = count("SELECT COUNT(DISTINCT order_id) FROM dbo.inv_stock_log WHERE log_type = 'SALE'")
    r.check_truth("DB-05", bad5 == 0 and sale_orders > 0,
                  "0 违例（配方 + 加料耗用，双向比对）",
                  f"{sale_orders} 张有 SALE 流水的订单，{bad5} 处与耗用不符")

    # DB-06 会员积分/余额快照与流水一致
    rows = r.all("""
        SELECT m.member_id, m.points,
               ISNULL((SELECT SUM(CASE WHEN p.point_type = 'REVERSAL' THEN -p.points ELSE p.points END)
                       FROM dbo.member_point_log p WHERE p.member_id = m.member_id), 0),
               m.balance,
               ISNULL((SELECT SUM(CASE WHEN b.balance_type = 'SPEND' THEN -b.amount ELSE b.amount END)
                       FROM dbo.member_balance_log b WHERE b.member_id = m.member_id), 0)
        FROM dbo.member_member m""")
    bad6 = [x for x in rows if int(x[1]) != int(x[2]) or _d(x[3]) != _d(x[4])]
    r.check_truth("DB-06", not bad6, "积分/余额 == 流水汇总",
                  f"{len(bad6)} 位会员不一致" + (f"：{bad6}" if bad6 else ""))

    # DB-07 约束启用
    disabled = count("SELECT COUNT(*) FROM sys.foreign_keys WHERE is_disabled = 1") + \
        count("SELECT COUNT(*) FROM sys.check_constraints WHERE is_disabled = 1")
    r.check_truth("DB-07", disabled == 0, "0 禁用", f"{disabled} 项被禁用")

    # DB-08 孤儿行
    orphans = count("SELECT COUNT(*) FROM dbo.order_item i "
                    "WHERE NOT EXISTS (SELECT 1 FROM dbo.shop_order o WHERE o.order_id = i.order_id)") + \
        count("SELECT COUNT(*) FROM dbo.order_item_addon a "
              "WHERE NOT EXISTS (SELECT 1 FROM dbo.order_item i WHERE i.order_id = a.order_id "
              "AND i.item_line = a.item_line)")
    r.check_truth("DB-08", orphans == 0, "0 条", f"{orphans} 条孤儿行")

    # DB-09 单号/支付流水号唯一
    dup_orders = count("SELECT COUNT(*) FROM (SELECT order_id FROM dbo.shop_order "
                       "GROUP BY order_id HAVING COUNT(*) > 1) t")
    dup_txn = count("SELECT COUNT(*) FROM (SELECT pay_txn_no FROM dbo.shop_order "
                    "WHERE pay_txn_no IS NOT NULL GROUP BY pay_txn_no HAVING COUNT(*) > 1) t")
    r.check_truth("DB-09", dup_orders == 0 and dup_txn == 0, "0 重复",
                  f"单号重复 {dup_orders}，流水号重复 {dup_txn}")

    # DB-10 加料快照不随现价变化：改现价后**旧单快照不变、新单按新价落快照**
    old_snaps = r.all("SELECT order_id, item_line, material_id, addon_price "
                      "FROM dbo.order_item_addon ORDER BY order_id, item_line, material_id")
    old_price = r.addon_price("P0000003", "M0000004")
    r.cur.execute("UPDATE dbo.item_addon_option SET addon_price = ? "
                  "WHERE product_id = 'P0000003' AND material_id = 'M0000004'", old_price + 1)
    new_order = _cash_order(r)                      # _STOCK_ORDER 带 M0000004 加料
    new_snap = r.one("SELECT addon_price FROM dbo.order_item_addon WHERE order_id = ?", new_order)
    old_after = r.all("SELECT order_id, item_line, material_id, addon_price "
                      "FROM dbo.order_item_addon WHERE order_id <> ? "
                      "ORDER BY order_id, item_line, material_id", new_order)
    r.check_truth("DB-10",
                  old_after == old_snaps and _d(new_snap) == _d(old_price + 1),
                  "旧单快照不变；新单快照 = 改后现价",
                  f"现价 {old_price}→{old_price + 1}；旧单 {len(old_snaps)} 行不变={old_after == old_snaps}；"
                  f"新单 {new_order} 快照={new_snap}")


# ---------------------------------------------------------------------------
# 工具：sqlcmd / 直连 / 测试库
# ---------------------------------------------------------------------------
def sqlcmd(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SQLCMD), "-S", Config.DB_SERVER, "-E", "-C", "-b", "-f", "65001", *args],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def sqlcmd_query(sql: str) -> subprocess.CompletedProcess:
    return sqlcmd("-d", "master", "-Q", sql)


def direct(db_name: str | None = None, autocommit: bool = False):
    """用 Windows 信任连接直连（沙箱/J 组核对用）。"""
    name = db_name or Config.DB_NAME
    cs = (f"DRIVER={{{Config.DB_DRIVER}}};SERVER={Config.DB_SERVER};DATABASE={name};"
          "Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate=yes;")
    return pyodbc.connect(cs, autocommit=autocommit, timeout=10)


def snapshot_structure(cur) -> dict:
    """结构指纹 + 17 表行数，用于 REPRO-01 重建前后比对。

    参数是**游标**而不是连接：REPRO-02 必须复用主 harness 的游标，否则新开连接会
    撞上本轮事务尚未提交的写锁（真会一直等下去）。
    """
    def n(sql: str) -> int:
        cur.execute(sql)
        return int(cur.fetchone()[0])
    snap = {
        "columns": n("SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id"),
        "pk": n("SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK'"),
        "uq": n("SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ'"),
        "uq_f": n("SELECT COUNT(*) FROM sys.indexes i WHERE i.is_unique = 1 AND i.is_primary_key = 0 "
                  "AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0 "
                  "AND i.object_id IN (SELECT object_id FROM sys.tables)"),
        "fk": n("SELECT COUNT(*) FROM sys.foreign_keys"),
        "ck": n("SELECT COUNT(*) FROM sys.check_constraints"),
        "df": n("SELECT COUNT(*) FROM sys.default_constraints"),
    }
    tables = ["staff_employee", "member_member", "member_point_log", "member_balance_log",
              "mkt_coupon_rule", "mkt_member_coupon", "item_product", "item_material",
              "item_recipe", "item_addon_option", "inv_stock_log", "inv_restock",
              "inv_restock_item", "shop_order", "order_item", "order_item_addon", "order_platform"]
    snap["rows"] = {t: n(f"SELECT COUNT(*) FROM dbo.{t}") for t in tables}
    snap["total"] = sum(snap["rows"].values())
    return snap


# ---------------------------------------------------------------------------
# I 组：并发、幂等与故障注入（沙箱 MilkTeaShop_test，真提交）
# ---------------------------------------------------------------------------
def _sandbox_conn(autocommit: bool = True):
    return direct(TEST_DB, autocommit=autocommit)


def _sandbox_patch_stock(materials: dict, stock: Decimal, reserved: Decimal = Decimal("0")) -> None:
    conn = _sandbox_conn()
    try:
        cur = conn.cursor()
        for material_id in materials:
            cur.execute("UPDATE dbo.item_material SET stock_qty = ?, reserved_qty = ? WHERE material_id = ?",
                        stock, reserved, material_id)
        conn.commit()
    finally:
        conn.close()


def _sandbox_new_order(client) -> str:
    """在沙箱里下一张自助单并返回**新单号**；未真落单则抛 CaseFailure。

    CONC-02/03 早期直接 ``SELECT TOP 1 order_id ... ORDER BY order_id DESC`` 取对象，
    下单失败时会拿到上一轮的旧单：旧单若恰好满足"已支付 + 积分差额"或"已取消 + 预占不变"，
    断言就会**静默记 PASS**。这里先校验 302 且订单数 +1，再按 Location 头取单号
    （拿不到才退回 TOP 1，此时订单数已确认多了一单）。
    """
    conn = _sandbox_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
        before = int(cur.fetchone()[0])
    finally:
        conn.close()
    resp = client.post("/orders/", data={"channel": "COUNTER", "order_mode": "COUNTER_SELF",
                                         "pay_method": "ONLINE", "qty.P0000003": "1"})
    conn = _sandbox_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
        after = int(cur.fetchone()[0])
        if resp.status_code != 302 or after != before + 1:
            raise CaseFailure(
                f"沙箱自助单未创建：status={resp.status_code} 订单数 {before}→{after}")
        loc = resp.headers.get("Location", "")
        order_id = loc.rstrip("/").rsplit("/", 1)[-1]
        if not order_id:
            cur.execute("SELECT TOP 1 order_id FROM dbo.shop_order ORDER BY order_id DESC")
            order_id = cur.fetchone()[0]
        return order_id
    finally:
        conn.close()


def _run_threads(app, tasks: list) -> list:
    """并发跑若干个 (用户名, 方法, 路径, 表单) 任务，返回 [(状态码, 位置头)]。

    **任何线程异常/join 超时都折成状态码 0**，让该轮判为不满足不变量（而不是让槽位停在
    ``None``、被下游 ``None[0]`` 抛 TypeError 把整轮证据崩掉）。异常文本放进位置头，
    便于在证据备注里看到原因。早期版本没有这层兜底：I 组一旦出错是"证据文件根本不写出"，
    而不是"这条用例 FAIL"。
    """
    results: list = [None] * len(tasks)
    barrier = threading.Barrier(len(tasks))

    def worker(index: int, task: dict) -> None:
        try:
            client = app.test_client()
            if task.get("username"):
                client.post("/login", data={"username": task["username"],
                                            "password": PASSWORD[task["username"]]})
            barrier.wait()
            if task["method"] == "GET":
                resp = client.get(task["path"])
            else:
                resp = client.post(task["path"], data=task.get("data") or {})
            results[index] = (resp.status_code, resp.headers.get("Location", ""))
        except Exception as exc:                # noqa: BLE001 —— 线程内异常必须落在该槽位
            results[index] = (0, f"THREAD-ERR:{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=worker, args=(i, t), daemon=True)
               for i, t in enumerate(tasks)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    alive = [t for t in threads if t.is_alive()]
    for i, slot in enumerate(results):
        if slot is None:
            results[i] = (0, "THREAD-TIMEOUT:线程未在 60s 内返回")
    if alive:
        # 只记录事实：让当轮判失败，不再静默继续
        print(f"[I 组] 警告：{len(alive)} 个线程 join 超时，已按状态码 0 计入本轮", flush=True)
    return results


def group_concurrency(r: Runner, enabled: bool) -> None:
    """I 组：沙箱真提交 + 真并发，每条跑 CONC_ROUNDS 轮（全部轮次都满足不变量才算 PASS）。

    并发用例只断言**不变量**（谁赢不确定），不锁死状态码序列：
    例如重复支付按设计允许"幂等成功"或"409"两种，只要库存/积分只变一次即可。
    """
    if not enabled:
        for cid in ("CONC-01", "CONC-02", "CONC-03", "CONC-04", "CONC-05", "CONC-06"):
            r.rec_skip(cid, "真并发", "未执行",
                       "I 组需要真提交沙箱，加 --concurrency 运行（会先建 MilkTeaShop_test）")
        return

    CONC_ROUNDS = 3
    saved_name = Config.DB_NAME
    Config.DB_NAME = TEST_DB
    try:
        app = create_app()
        app.config["SERVER_NAME"] = "localhost"

        conn = _sandbox_conn(autocommit=False)
        try:
            cur = conn.cursor()
            cur.execute("SELECT material_id, qty FROM dbo.item_recipe WHERE product_id = 'P0000003'")
            usage = {row[0]: _d(row[1]) for row in cur.fetchall()}
        finally:
            conn.close()
        total_usage = sum(usage.values(), Decimal("0"))
        first_material = list(usage)[0]
        seq = {"n": 0}

        def _uniq(prefix: str) -> str:
            """每轮都要换唯一键（如支付流水号），否则第 2 轮开始会撞"流水号已用于其它订单"。"""
            seq["n"] += 1
            return f"{prefix}-{seq['n']}"

        # --- 各条用例的单轮实现：返回 (是否满足不变量, 说明, 状态码列表) ---------------
        def round_conc01() -> tuple[bool, str]:
            """末件库存：把每种原料调成"刚好一杯"，两个并发请求只能成一个。"""
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                for material_id, qty in usage.items():
                    cur.execute("UPDATE dbo.item_material SET stock_qty = ?, reserved_qty = 0 "
                                "WHERE material_id = ?", qty, material_id)
                conn.commit()
                cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
                before = int(cur.fetchone()[0])
            finally:
                conn.close()
            results = _run_threads(app, [
                {"username": "customer1", "method": "POST", "path": "/orders/",
                 "data": {"channel": "COUNTER", "order_mode": "COUNTER_SELF",
                          "pay_method": "ONLINE", "qty.P0000003": "1"}} for _ in range(2)])
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
                after = int(cur.fetchone()[0])
                cur.execute("SELECT stock_qty, reserved_qty FROM dbo.item_material")
                rows = cur.fetchall()
            finally:
                conn.close()
            codes = sorted(c[0] for c in results)
            invariants = all(_d(r[1]) <= _d(r[0]) and _d(r[1]) >= 0 for r in rows)
            ok = codes == [302, 409] and after - before == 1 and invariants
            return ok, f"{codes} 订单+{after - before} 不超卖={invariants}", codes

        def round_conc02() -> tuple[bool, str]:
            """重复支付：同一 PENDING 单、同一 pay_txn_no 并发支付，只生效一次。"""
            _sandbox_patch_stock(usage, stock=Decimal("10000"))
            client = app.test_client()
            client.post("/login", data={"username": "customer1", "password": PASSWORD["customer1"]})
            pay_id = _sandbox_new_order(client)
            txn_no = _uniq("⑦CONC-DUP")
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT points FROM dbo.member_member WHERE member_id = 'C0000001'")
                points_before = int(cur.fetchone()[0])
                cur.execute("SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", first_material)
                stock_before = _d(cur.fetchone()[0])
            finally:
                conn.close()
            results = _run_threads(app, [
                {"username": "customer1", "method": "POST", "path": f"/orders/{pay_id}/pay",
                 "data": {"pay_method": "ONLINE", "pay_txn_no": txn_no}} for _ in range(2)])
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT order_status, total_amount FROM dbo.shop_order WHERE order_id = ?", pay_id)
                status_now, total = cur.fetchone()
                cur.execute("SELECT points FROM dbo.member_member WHERE member_id = 'C0000001'")
                points_after = int(cur.fetchone()[0])
                cur.execute("SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", first_material)
                stock_after = _d(cur.fetchone()[0])
            finally:
                conn.close()
            expected_points = int(_d(total))
            codes = [c for c, _ in results]
            ok = (all(c in (302, 409) for c in codes) and status_now == "PAID"
                  and points_after - points_before == expected_points
                  and stock_before - stock_after == usage[first_material])
            return ok, (f"{codes} 状态={status_now} 积分+{points_after - points_before}"
                        f"（应+{expected_points}）库存-{stock_before - stock_after}"), codes

        def round_conc03() -> tuple[bool, str]:
            """同一 PENDING 单并发取消：只释放一次预占。"""
            _sandbox_patch_stock(usage, stock=Decimal("10000"))
            client = app.test_client()
            client.post("/login", data={"username": "customer1", "password": PASSWORD["customer1"]})
            cancel_id = _sandbox_new_order(client)
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT SUM(reserved_qty) FROM dbo.item_material")
                reserved_before = _d(cur.fetchone()[0])
            finally:
                conn.close()
            results = _run_threads(app, [
                {"username": "clerk1", "method": "POST", "path": f"/orders/{cancel_id}/cancel",
                 "data": {}} for _ in range(2)])
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT order_status FROM dbo.shop_order WHERE order_id = ?", cancel_id)
                status_now = cur.fetchone()[0]
                cur.execute("SELECT SUM(reserved_qty) FROM dbo.item_material")
                reserved_after = _d(cur.fetchone()[0])
            finally:
                conn.close()
            codes = sorted(c for c, _ in results)
            ok = (codes == [302, 409] and status_now == "CANCELLED"
                  and reserved_before - reserved_after == total_usage)
            return ok, f"{codes} 状态={status_now} 预占 {reserved_before}→{reserved_after}", codes

        def round_conc04() -> tuple[bool, str]:
            """同日 4 线程并发下单：单号全唯一、无 2627 逃逸。"""
            _sandbox_patch_stock(usage, stock=Decimal("100000"))
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
                before = int(cur.fetchone()[0])
            finally:
                conn.close()
            results = _run_threads(app, [
                {"username": "clerk1", "method": "POST", "path": "/orders/",
                 "data": {"channel": "COUNTER", "order_mode": "COUNTER_CASHIER",
                          "pay_method": "CASH", "qty.P0000003": "1"}} for _ in range(4)])
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT order_id FROM dbo.shop_order")
                ids = [row[0] for row in cur.fetchall()]
            finally:
                conn.close()
            codes = [c for c, _ in results]
            unique = len(set(ids)) == len(ids)
            added = len(ids) - before
            ok = codes == [302, 302, 302, 302] and unique and added == 4
            return ok, f"{codes} 新增{added}单 单号唯一={unique}", codes

        def round_conc05() -> tuple[bool, str]:
            """并发库存调整 +10/-10：终值正确、本轮流水净额 0。"""
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT stock_qty FROM dbo.item_material WHERE material_id = 'M0000002'")
                start = _d(cur.fetchone()[0])
                cur.execute("SELECT ISNULL(MAX(stock_log_id), 0) FROM dbo.inv_stock_log")
                max_log_before = int(cur.fetchone()[0])
            finally:
                conn.close()
            results = _run_threads(app, [
                {"username": "stock1", "method": "POST", "path": "/inventory/adjust",
                 "data": {"material_id": "M0000002", "delta": "10"}},
                {"username": "stock1", "method": "POST", "path": "/inventory/adjust",
                 "data": {"material_id": "M0000002", "delta": "-10"}},
            ])
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT stock_qty FROM dbo.item_material WHERE material_id = 'M0000002'")
                end = _d(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*), SUM(CASE WHEN log_type = 'GAIN' THEN qty ELSE -qty END) "
                            "FROM dbo.inv_stock_log WHERE material_id = 'M0000002' AND stock_log_id > ?",
                            max_log_before)
                logs, net = cur.fetchone()
            finally:
                conn.close()
            codes = [c for c, _ in results]
            ok = (codes == [302, 302] and end == start
                  and int(logs) == 2 and _d(net) == _d(0))
            return ok, f"{start}→{end} 本轮流水{int(logs)}条 净额{net}", codes

        def round_conc06() -> tuple[bool, str]:
            """事务中途抛异常：订单/明细/加料/流水/预占全部回滚。"""
            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
                before_orders = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM dbo.inv_stock_log")
                before_logs = int(cur.fetchone()[0])
                cur.execute("SELECT SUM(reserved_qty) FROM dbo.item_material")
                before_reserved = _d(cur.fetchone()[0])
            finally:
                conn.close()

            real_item = order_svc._order.insert_order_item
            order_svc._order.insert_order_item = lambda *a, **k: (_ for _ in ()).throw(
                RuntimeError("⑦集成测试注入的异常（沙箱）"))
            try:
                order_svc.create_order(
                    items=[{"product_id": "P0000003", "qty": 1}],
                    role="clerk", employee_id="E0000001", channel="COUNTER",
                    order_mode="COUNTER_CASHIER", pay_method="CASH")
            except Exception:                       # noqa: BLE001 —— 任何异常都应导致整体回滚
                raised = True
            else:
                raised = False
            finally:
                order_svc._order.insert_order_item = real_item

            conn = _sandbox_conn()
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM dbo.shop_order")
                after_orders = int(cur.fetchone()[0])
                cur.execute("SELECT COUNT(*) FROM dbo.inv_stock_log")
                after_logs = int(cur.fetchone()[0])
                cur.execute("SELECT SUM(reserved_qty) FROM dbo.item_material")
                after_reserved = _d(cur.fetchone()[0])
            finally:
                conn.close()
            ok = (raised and after_orders == before_orders and after_logs == before_logs
                  and after_reserved == before_reserved)
            # 这条不经过 HTTP，没有状态码可判重试，故返回空列表
            return ok, (f"异常={raised} 订单{before_orders}→{after_orders} "
                        f"流水{before_logs}→{after_logs} 预占{before_reserved}→{after_reserved}"), []

        def judge(cid: str, expect: str, func) -> None:
            rounds = []
            for _ in range(CONC_ROUNDS):
                ok, note, codes = func()
                # 500（死锁牺牲者 E5003）属可重试的偶发情况：重跑一轮，最多 2 次。
                # **按状态码判**，不能按备注文本判：备注里含库存/预占的十进制值
                # （如 1500.00 / 500.00），用 `"500" in note` 会把任何失败都误判成可重试的 500，
                # 第二次通过就记 PASS，从而掩盖真实缺陷。
                tries = 1
                while not ok and 500 in codes and tries < 2:
                    ok, note, codes = func()
                    tries += 1
                    note += f"（重试{tries - 1}次）"
                rounds.append((ok, note))
            all_ok = all(item[0] for item in rounds)
            note = "；".join(f"第{i + 1}轮 {item[1]}" for i, item in enumerate(rounds))
            r.check_truth(cid, all_ok, expect, note)

        judge("CONC-01", "302+409/只落 1 单/不超卖", round_conc01)
        judge("CONC-02", "PAID/积分与库存只变一次", round_conc02)
        judge("CONC-03", "302+409/只释放一次", round_conc03)
        judge("CONC-04", "4 单全部成功、单号唯一", round_conc04)
        judge("CONC-05", "终值不变/本轮流水 2 条且净额 0", round_conc05)
        judge("CONC-06", "异常抛出且订单/流水/预占全部回滚", round_conc06)
    finally:
        Config.DB_NAME = saved_name


# ---------------------------------------------------------------------------
# J 组：复现与交付
# ---------------------------------------------------------------------------
def group_repro(r: Runner, *, do_repro: bool, real_server: bool) -> None:
    # REPRO-01 空库复现
    if not do_repro:
        r.rec_ref("REPRO-01", "6 步全 exit=0；重建前后结构计数与 17 表行数一致", "本轮未重跑",
                  "00-create.sql 会删库重建 v0.1，故默认不跑；加 --repro 执行。"
                  "该链条已在 trusted 版证据中实测通过（6 步全过、结构对象计数与 127 行数据一致）")
    else:
        if not SQLCMD.exists():
            r.rec_skip("REPRO-01", "全部 exit=0", "未执行", f"找不到 sqlcmd：{SQLCMD}")
        else:
            try:
                v01_conn = direct("MilkTeaShop")
                try:
                    before = snapshot_structure(v01_conn.cursor())
                finally:
                    v01_conn.close()
            except pyodbc.Error as exc:
                r.rec("REPRO-01", "全部 exit=0", "源库不可读", False, str(exc)[:120])
                before = None
            if before is not None:
                # ⚠ 安全闸门：备份没成功就绝不许往下走（下一步 00-create.sql 会 DROP 库）
                backup = sqlcmd_query(
                    "DECLARE @p nvarchar(4000) = CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000)) "
                    "+ N'\\MilkTeaShop_pre_repro.bak'; BACKUP DATABASE [MilkTeaShop] TO DISK = @p "
                    "WITH INIT, COPY_ONLY, CHECKSUM;")
                if backup.returncode != 0:
                    r.rec("REPRO-01", "6 步全 exit=0", "备份失败，已中止（未删库）", False,
                          ((backup.stdout or "") + (backup.stderr or "")).strip().splitlines()[-1][:160]
                          if ((backup.stdout or "") + (backup.stderr or "")).strip() else "")
                    return
                steps, failed_step = [], None
                for script in ("00-create", "01-schema", "constraint", "02-seed", "view", "role"):
                    proc = sqlcmd("-i", str(ROOT / "sql" / f"{script}.sql"))
                    steps.append((script, proc.returncode))
                    if proc.returncode != 0:
                        failed_step = (script, (proc.stdout or "") + (proc.stderr or ""))
                        break
                if failed_step:
                    restore = sqlcmd_query(
                        "DECLARE @p nvarchar(4000) = CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000)) "
                        "+ N'\\MilkTeaShop_pre_repro.bak'; DECLARE @d nvarchar(4000) = "
                        "CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(4000)) + N'\\'; "
                        "ALTER DATABASE [MilkTeaShop] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                        "RESTORE DATABASE [MilkTeaShop] FROM DISK = @p WITH REPLACE, RECOVERY, "
                        "MOVE N'MilkTeaShop' TO @d + N'MilkTeaShop.mdf', "
                        "MOVE N'MilkTeaShop_log' TO @d + N'MilkTeaShop_log.ldf';")
                    restored = "已回滚" if restore.returncode == 0 else "回滚也失败，需手工 RESTORE"
                    r.rec("REPRO-01", "6 步全 exit=0",
                          f"{failed_step[0]} 失败（{restored}）", False,
                          (failed_step[1].strip().splitlines()[-1][:120] if failed_step[1] else "")
                          + f"；RESTORE rc={restore.returncode}")
                else:
                    v01_conn = direct("MilkTeaShop")
                    try:
                        after = snapshot_structure(v01_conn.cursor())
                    finally:
                        v01_conn.close()
                    same = before == after
                    r.check_truth("REPRO-01",
                                  same and all(code == 0 for _, code in steps),
                                  "6 步全过且重建前后结构计数与 17 表行数一致",
                                  f"结构 {before['columns']}列/{before['ck']}CK 行数 {before['total']}，"
                                  f"重建后 {after['columns']}列/{after['ck']}CK 行数 {after['total']}")

    # REPRO-02 迁移 + 回归（沿用第 6 周证据 + 现状核对）
    # 用独立的信任连接做只读核对：sql 模式下主 harness 走的是 role_app（没有 staff_employee
    # 等表的 SELECT），拿不到行数；J 组排在最前面，此时主事务尚无写锁，不会被阻塞
    try:
        probe_conn = direct(Config.DB_NAME)
        try:
            snap = snapshot_structure(probe_conn.cursor())
        finally:
            probe_conn.close()
    except pyodbc.Error as exc:
        r.rec_skip("REPRO-02", "236/7 单/12 明细", "无法读取", f"只读核对连接失败：{_native_code(exc)}")
        snap = None
    if snap is not None:
        fingerprint = (snap["columns"] + snap["pk"] + snap["uq"] + snap["uq_f"]
                       + snap["fk"] + snap["ck"] + snap["df"])
        r.check_truth("REPRO-02",
                      fingerprint == 236 and snap["rows"]["shop_order"] == 7
                      and snap["rows"]["order_item"] == 12,
                      "236/7 单/12 明细（沿用第 6 周 04/18 证据）",
                      f"指纹 {fingerprint}，17 表 {snap['total']} 行（第 6 周口径 127 行）")

    # REPRO-03 启动 + /health
    proc = None
    try:
        if real_server:
            proc = subprocess.Popen([sys.executable, "run.py"], cwd=str(ROOT),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            payload, ok = None, False
            import json as _json
            import urllib.error
            import urllib.request
            for _ in range(40):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:5000/health", timeout=2) as resp:
                        payload = _json.loads(resp.read().decode("utf-8"))
                        ok = resp.status == 200
                        break
                except (urllib.error.URLError, OSError):
                    import time as _time
                    _time.sleep(0.5)
            fp = (payload or {}).get("fingerprint") or {}
            r.check_truth("REPRO-03",
                          ok and fp.get("total") == 236 and fp.get("match") is True,
                          "HTTP 200/total=236/match=true",
                          f"真实服务启动 {'成功' if ok else '失败'} payload={fp.get('total')}/{fp.get('match')}")
        else:
            resp = r.get("/health")
            import json as _json
            payload = _json.loads(resp.get_data(as_text=True))
            fp = payload.get("fingerprint") or {}
            r.check_truth("REPRO-03",
                          resp.status_code == 200 and fp.get("total") == 236 and fp.get("match") is True,
                          "200/total=236/match=true",
                          f"测试客户端 fingerprint={fp.get('total')}/{fp.get('match')}（未加 --real-server）")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    # REPRO-04 5 个账号登录
    results = {}
    for username in PASSWORD:
        client = r.app.test_client()
        resp = client.post("/login", data={"username": username, "password": PASSWORD[username]})
        with client.session_transaction() as sess:
            results[username] = (resp.status_code, sess.get("role"))
    ok = all(code == 302 and role for code, role in results.values())
    r.check_truth("REPRO-04", ok, "5/5 登录成功",
                  ", ".join(f"{u}:{code}/{role}" for u, (code, role) in results.items()))

    # REPRO-05 README 命令可照抄（静态核对：命令里引用的脚本/文件都存在）
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    manual = ROOT / "docs/09-应用开发/操作手册.md"
    manual_text = manual.read_text(encoding="utf-8") if manual.exists() else ""
    # 覆盖全部可复跑自检脚本、沙箱 SQL、配置与两个入口文档；比早期"只核对 6 个路径"更贴近用例名
    referenced = ["sql/app_login.sql", "sql/migration.sql", "sql/regression.sql",
                  "requirements.txt", ".env.example", "run.py",
                  "scripts/smoke_test.py", "scripts/verify_db_logins.py",
                  "scripts/dao_test.py", "scripts/dao_roles_test.py",
                  "scripts/order_service_test.py", "scripts/order_pages_test.py",
                  "scripts/permission_matrix_test.py", "scripts/integration_test.py",
                  "scripts/create_v01_copy.sql", "scripts/create_test_db.sql",
                  "docs/09-应用开发/操作手册.md"]
    missing = [p for p in referenced if not (ROOT / p).exists()]
    not_in_readme = [p for p in referenced if p not in readme]
    r.check_truth("REPRO-05",
                  not missing and not not_in_readme,
                  "README/操作手册 含全部命令且文件存在",
                  f"缺失文件={missing} README 未引用={not_in_readme} 手册={'有' if manual_text else '缺'}")

    # REPRO-06 证据脱敏
    proc = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True)
    tracked = proc.stdout.splitlines()
    env_tracked = [p for p in tracked if p.endswith(".env")]
    secrets = [p for p in PASSWORD.values()] + list(_SQL_LOGIN_PWD.values())
    hits = []
    for folder in ("result", "logs"):
        for path in (ROOT / folder).glob("*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(s in text for s in secrets):
                hits.append(path.name)
    r.check_truth("REPRO-06",
                  not env_tracked and not hits,
                  "无 .env 入库、证据无真实密码",
                  f"入库 .env={env_tracked} 含密码的文件={hits}")


# ---------------------------------------------------------------------------
# 证据渲染与入口
# ---------------------------------------------------------------------------
def render(r: Runner, *, sql_mode: bool, concurrency: bool, real_server: bool, do_repro: bool) -> str:
    lines: list[str] = []
    w = lines.append
    rows = r.rows
    total = len(rows)
    passed = sum(1 for v in rows.values() if v[2] == "PASS")
    failed = sum(1 for v in rows.values() if v[2] == "FAIL")
    skipped = sum(1 for v in rows.values() if v[2] == "SKIP")
    referred = sum(1 for v in rows.values() if v[2] == "REF")

    w("=" * 108)
    w("第 7—9 周 ⑦ 集成测试与异常验证：A–J 组逐条执行（对应 docs/09-应用开发/测试用例.md §5）")
    w("=" * 108)
    w(f"生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    w(f"Python {sys.version.split()[0]}　DB_AUTH={'sql' if sql_mode else Config.DB_AUTH}"
      f"　DB_NAME={Config.DB_NAME}")
    if sql_mode:
        w("执行方式：本轮＝DB 层权限探针（PERM-16/17/18、ERR-06）+ I 组沙箱真提交 + J 组；"
          "A–H 组按用例 §2「功能测试走 trusted」不重复执行（标 REF，见同目录另一份证据）")
    else:
        w("执行方式：A–H 组＝Flask 测试客户端 + 外层事务 + SAVEPOINT（整轮回滚，seed 不变）；"
          f"I 组＝{'沙箱 ' + TEST_DB + ' 真提交' if concurrency else '未执行（需 --concurrency）'}")
    w(f"可选开关：--concurrency={'是' if concurrency else '否'}　"
      f"--real-server={'是' if real_server else '否'}　--repro={'是' if do_repro else '否'}")
    w("")

    for group in GROUP_ORDER:
        ids = sorted((cid for cid in rows if cid.split("-")[0] == group),
                     key=lambda cid: int(cid.split("-")[1]))
        if not ids:
            continue
        w(GROUP_TITLE[group])
        w("-" * 108)
        w(f"{'用例':<14}{'预期':<26}{'实际':<26}{'结果':<6}备注")
        for cid in ids:
            expect, actual, status, note = rows[cid]
            # 不截断：截断会吃掉 CONC 的三轮说明、REPRO-01 的关键数字、DB-01 的 CHECK/DEFAULT
            w(f"{cid:<14}{expect:<30}{actual:<40}{status:<6}{note}")
        w("")

    w("-" * 108)
    w(f"总计：{total} 项，PASS {passed}，FAIL {failed}，SKIP {skipped}，REF {referred}")
    w("")
    w("口径说明：")
    w("  · SKIP＝当前开关/认证模式下不成立的用例（如 trusted 下的 DB 层权限项、未加 --concurrency 的 I 组）；")
    w("  · REF＝本轮范围内不重复执行、由另一份证据覆盖（sql 版：A–H 组已在 trusted 版全量执行；"
      "未加 --repro 时 REPRO-01 也标 REF）；")
    w("  · 每行的「预期/实际」可能含多段（同一用例做了多条断言，已合并）；")
    w("  · 失败处理按 §7：P0 阻断交付并全量重跑，P1 记录缺陷与影响范围，P2 记入已知限制。")
    w("=" * 108)
    # SKIP 是「本轮不成立、未执行」，不能算进“实跑”；REF 同理（单独统计）。
    executed = passed + failed
    if sql_mode:
        w(f"结论：本轮实跑 {executed} 项（PASS {passed}，FAIL {failed}，SKIP {skipped}）；"
          f"另 {referred} 条 REF 本轮未执行，见 trusted 版证据")
    elif failed == 0:
        w(f"结论：全部通过（无 FAIL）；实跑 {executed} 项，REF {referred} 项")
    else:
        w(f"结论：存在 {failed} 项 FAIL，需修复后重跑（实跑 {executed} 项，REF {referred} 项）")
    w("=" * 108)
    return "\n".join(lines) + "\n"


def main() -> int:
    write = "--write" in sys.argv
    sql_mode = "--sql-auth" in sys.argv
    concurrency = "--concurrency" in sys.argv
    real_server = "--real-server" in sys.argv
    do_repro = "--repro" in sys.argv
    only = [a.split("=", 1)[1] for a in sys.argv if a.startswith("--only=")]

    if sql_mode:
        Config.DB_AUTH = "sql"
        for key, value in _SQL_LOGIN_PWD.items():
            os.environ.setdefault(key, value)

    # I 组沙箱：先建测试库（只删/建 MilkTeaShop_test，源库只读）
    sandbox_ok = False
    if concurrency:
        if not SQLCMD.exists():
            print(f"[SKIP] 找不到 sqlcmd，I 组跳过：{SQLCMD}")
        else:
            proc = sqlcmd("-i", str(ROOT / "scripts" / "create_test_db.sql"))
            sandbox_ok = proc.returncode == 0
            if not sandbox_ok:
                print("[FAIL] 建测试库失败：", ((proc.stdout or "") + (proc.stderr or ""))[-400:])

    r = Runner()
    groups = [("AUTH", group_auth), ("PERM", group_perm), ("PROD", group_product),
              ("INV", group_inventory), ("ORD", group_order), ("QRY", group_query),
              ("ERR", group_error), ("DB", group_db)]

    # sql 模式只覆盖"数据库层权限"那几条（用例 §2：功能测试走 trusted，DB 层权限测试走 sql）。
    # 原因：本轮 harness 把三处 db_cursor 换成**同一条外层连接**（sql 模式下是 role_app 登录），
    # 因此其它角色的 SELECT 在下层会被 229 拒掉——那不是应用缺陷，而是 harness 的单连接限制。
    # 与其用这种方式凑一份"看起来全绿"的 sql 证据，不如如实只跑 DB 层权限项 + 沙箱并发项 + J 组，
    # 其余标注 REF（已在 trusted 全量执行，见同目录的 -sql认证 之外的证据文件）。
    sql_scope = {"CONC", "REPRO"}
    try:
        # J 组先跑：REPRO-02 的"现状核对"必须在业务数据被本轮改动之前做（否则行数会被算进本轮新增）
        if not only or "REPRO" in only:
            print("[run] 组 REPRO …", file=sys.stderr, flush=True)
            group_repro(r, do_repro=do_repro, real_server=real_server)

        # sql 模式的数据库层权限探针紧随其后（此时业务数据还没被本轮改动）
        if not only or "PERM" in only:
            sql_permission_probes(r)
        def _run_group(name: str, func) -> None:
            """分组级隔离：某组抛异常时，把该组**尚未登记**的用例记成 FAIL，
            既不会整轮崩掉丢证据，也不会让没跑到的用例凭空变成"没记录"。"""
            print(f"[run] 组 {name} …", file=sys.stderr, flush=True)
            try:
                func(r)
            except Exception as exc:                      # noqa: BLE001 —— 兜底，如实记为 FAIL
                pending = [cid for cid in CASES
                           if cid.split("-")[0] == name and cid not in r.rows]
                for cid in pending:
                    r.rec(cid, "执行完成", f"组内异常中止：{type(exc).__name__}", False,
                          f"{exc}"[:160])
                print(f"[FAIL] 组 {name} 异常中止：{exc}", file=sys.stderr, flush=True)

        for name, func in groups:
            if only and name not in only:
                continue
            if sql_mode and name not in sql_scope:
                continue
            _run_group(name, func)

        # I 组：临时还原服务连接，让并发用例走真实连接、真提交
        if not only or "CONC" in only:
            saved = (order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor)
            order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor = r._orig
            print("[run] 组 CONC …", file=sys.stderr, flush=True)
            try:
                group_concurrency(r, concurrency and sandbox_ok)
            finally:
                order_svc.db_cursor, product_svc.db_cursor, inventory_svc.db_cursor = saved

    finally:
        r.close()

    if sql_mode:
        for cid in CASES:
            if cid not in r.rows:
                r.rec_ref(cid, "见 trusted 版证据", "本轮未执行",
                          "sql 模式按用例 §2 只跑数据库层权限项（同连接 harness 无法覆盖其它角色读写）")

    text = render(r, sql_mode=sql_mode, concurrency=concurrency and sandbox_ok,
                  real_server=real_server, do_repro=do_repro)
    print(text)
    for cid, (expect, actual, status, note) in r.rows.items():
        if status == "FAIL":
            print(f"[FAIL] {cid}: 预期 {expect} / 实际 {actual} / {note}")

    failed = sum(1 for v in r.rows.values() if v[2] == "FAIL")
    missing = set(CASES) - set(r.rows)
    if missing:
        print(f"[WARN] 以下 {len(missing)} 条用例本轮未执行：{sorted(missing)}")

    if write:
        suffix = "-sql认证" if sql_mode else ""
        path = ROOT / "result" / f"第8周-⑦集成测试-{dt.date.today():%Y%m%d}{suffix}.txt"
        path.write_text(text, encoding="utf-8")
        print("evidence:", os.path.relpath(path, ROOT))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
