"""第十一周 ⑦ 应用回归：真实服务链路 + 触发器 + 窗口函数一致性。

用法（仓库根目录，先建 week11 验收库）：

    $env:DB_NAME = 'MilkTeaShop_week11_<名>'
    python scripts/week11_app_test.py            # 只打印
    python scripts/week11_app_test.py --write    # 另写 result/第11周-⑦应用回归.txt

做的是「页面层以下」的真实调用：order_service 下单/支付/制作/完成、
inventory_service 库存调整，全部走 app/dao 的真实 SQL；审计触发器与订单完成触发器
在 v2.3 验收库上真实生效。

事务策略：沿用 order_service_test.py 的做法，把服务层的 db_cursor 换成
“同一连接 + SAVE TRANSACTION”，全部用例结束后整体回滚，不污染验收库。

说明：本机 SQL Server 当前 LoginMode=1（仅 Windows 认证，非管理员无法改），
所以本轮应用回归跑 trusted；role_customer/role_app 等受限账户分支由
week11_trigger_test.sql 的 EXECUTE AS USER 用例覆盖，SQL 登录分支待混合认证开启后补跑。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.dao.connection import connect, probe_database, schema_version  # noqa: E402
from app.errors import AppError, ErrCode  # noqa: E402
import app.services.inventory_service as isvc  # noqa: E402
import app.services.order_service as osvc  # noqa: E402

MEMBER_ID = "C0000002"
STOCK_EMPLOYEE = "E0000003"
PRODUCT_ID = "P0000004"      # 椰果奶茶
ADDON_ID = "M0000001"        # 珍珠，P0000004 的可选加料，2.00/份


class Harness:
    """同一连接 + savepoint 的假 db_cursor，服务层提交保留到外层 rollback。"""

    def __init__(self) -> None:
        self.conn = connect(None)
        self.cur = self.conn.cursor()
        self._orig = {}

    def __enter__(self) -> "Harness":
        @contextmanager
        def fake(role=None, commit=False):
            self.cur.execute("SAVE TRANSACTION sp_call")
            try:
                yield self.cur
            except BaseException:
                self.cur.execute("ROLLBACK TRANSACTION sp_call")
                raise
            else:
                if not commit:
                    self.cur.execute("ROLLBACK TRANSACTION sp_call")

        self._orig = {"order": osvc.db_cursor, "inventory": isvc.db_cursor}
        osvc.db_cursor = fake
        isvc.db_cursor = fake
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.conn.rollback()
        finally:
            osvc.db_cursor = self._orig["order"]
            isvc.db_cursor = self._orig["inventory"]
            self.cur.close()
            self.conn.close()

    # ---- helpers ----
    def one(self, sql: str, *params):
        self.cur.execute(sql, *params)
        return self.cur.fetchone()

    def scalar(self, sql: str, *params):
        row = self.one(sql, *params)
        return row[0] if row else None

    def stock(self, material_id: str) -> Decimal:
        return Decimal(str(self.scalar(
            "SELECT stock_qty FROM dbo.item_material WHERE material_id = ?", material_id
        ))).quantize(Decimal("0.01"))

    def reserved(self, material_id: str) -> Decimal:
        return Decimal(str(self.scalar(
            "SELECT reserved_qty FROM dbo.item_material WHERE material_id = ?", material_id
        ))).quantize(Decimal("0.01"))

    def max_audit(self) -> int:
        return int(self.scalar("SELECT ISNULL(MAX(audit_id), 0) FROM dbo.inv_stock_audit"))

    def audit_after(self, audit_id: int) -> list:
        self.cur.execute(
            "SELECT material_id, before_qty, after_qty, change_qty, change_type, "
            "operator_id, operator_source FROM dbo.inv_stock_audit "
            "WHERE audit_id > ? ORDER BY audit_id",
            audit_id,
        )
        return [tuple(r) for r in self.cur.fetchall()]

    def sale_rows(self, order_id: str) -> int:
        return int(self.scalar(
            "SELECT COUNT(*) FROM dbo.inv_stock_log WHERE order_id = ? AND log_type = 'SALE'",
            order_id,
        ))


class Reporter:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures = 0

    def log(self, text: str = "") -> None:
        self.lines.append(text)
        print(text)

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        if cond:
            self.log(f"PASS {name}" + (f"（{detail}）" if detail else ""))
        else:
            self.failures += 1
            self.log(f"FAIL {name}" + (f"（{detail}）" if detail else ""))


def _expect_app_error(name: str, fn, rep: Reporter) -> None:
    try:
        fn()
    except AppError as exc:
        rep.check(name, exc.code == ErrCode.BUSINESS_RULE, f"{exc.code}")
    except Exception as exc:  # noqa: BLE001
        rep.check(name, False, f"抛了非 AppError：{exc!r}")
    else:
        rep.check(name, False, "应当拒绝但成功了")


def main() -> int:
    if set(sys.argv[1:]) - {"--write"}:
        raise SystemExit("仅支持 --write。真实 SQL 角色连接请使用 order_pages_test.py --sql-auth。")

    db = Config.DB_NAME
    if not db.startswith("MilkTeaShop_week11_"):
        print(f"拒绝运行：DB_NAME={db!r} 不是 week11 验收库。")
        return 2

    rep = Reporter()
    started = dt.datetime.now()
    rep.log("=" * 96)
    rep.log("第 11 周 ⑦ 应用回归：下单→支付→制作→完成 + 审计触发器 + 窗口函数一致性")
    rep.log("=" * 96)
    rep.log(f"生成时间：{started:%Y-%m-%d %H:%M:%S}")
    rep.log(f"连接：DB_AUTH={Config.DB_AUTH}  DB_NAME={db}  DB_SERVER={Config.DB_SERVER}")
    rep.log("")

    try:
        with Harness() as h:
            version = schema_version(h.cur)
            rep.log(f"【0】库版本：{version}")
            rep.check("0.1 库版本为 2.3", version == "2.3", version)

            probe = probe_database()
            rep.check(
                "0.2 /health 指纹匹配",
                probe["schema_version"] == "2.3" and probe["fingerprint"]["match"],
                f"{probe['schema_version']} / {probe['fingerprint']['total']}",
            )

            rep.log("")
            rep.log("【1】顾客自助下单（PENDING，只预占）")
            stock_before = h.stock(ADDON_ID)
            reserved_before = h.reserved(ADDON_ID)
            audit_before_create = h.max_audit()
            detail = osvc.create_order(
                items=[{
                    "product_id": PRODUCT_ID,
                    "qty": 2,
                    "sugar_level": "HALF",
                    "ice_level": "LESS",
                    "temp_level": "COLD",
                    "addons": [{"material_id": ADDON_ID, "qty": 1}],
                }],
                role="customer",
                member_id=MEMBER_ID,
            )
            order_id = detail["order"]["order_id"]
            rep.check("1.1 下单成功且为 PENDING", detail["order"]["order_status"] == "PENDING", order_id)
            usages = {}
            h.cur.execute(
                "SELECT material_id, qty FROM dbo.order_material_usage WHERE order_id = ?",
                order_id,
            )
            usages = {r[0]: Decimal(str(r[1])).quantize(Decimal("0.01")) for r in h.cur.fetchall()}
            rep.check("1.2 已写下单耗用快照", len(usages) > 0, f"{len(usages)} 种原料")
            rep.check(
                "1.3 待支付只加预占、不减现存量",
                h.stock(ADDON_ID) == stock_before and h.reserved(ADDON_ID) == reserved_before + usages[ADDON_ID],
                f"stock {stock_before}→{h.stock(ADDON_ID)}，reserved {reserved_before}→{h.reserved(ADDON_ID)}",
            )
            rep.check("1.4 预占不产生审计行", h.max_audit() == audit_before_create)

            rep.log("")
            rep.log("【2】线上支付（确认扣料 + 写 SALE + 审计操作人）")
            audit_before_pay = h.max_audit()
            tx = f"W11-{started:%Y%m%d%H%M%S}"
            paid = osvc.pay_order(
                order_id, pay_method="ONLINE", pay_txn_no=tx, role="customer", member_id=MEMBER_ID
            )
            rep.check("2.1 支付后状态 PAID", paid["order"]["order_status"] == "PAID")
            rep.check(
                "2.2 现存量按快照扣减、预占清零",
                h.stock(ADDON_ID) == stock_before - usages[ADDON_ID],
                f"{stock_before}→{h.stock(ADDON_ID)}",
            )
            rep.check("2.3 SALE 流水与耗用原料数一致", h.sale_rows(order_id) == len(usages), f"{len(usages)} 条")
            audits = h.audit_after(audit_before_pay)
            rep.check("2.4 每个耗用原料恰好一条审计", len(audits) == len(usages), f"{len(audits)} 条")
            rep.check(
                "2.5 顾客支付审计使用数据库登录名并记录库存变化",
                all(r[5] is None and r[6] == "LOGIN" for r in audits)
                and all(r[3] == -usages[r[0]] for r in audits),
            )

            rep.log("")
            rep.log("【3】制作流转与完成（不得再次扣料）")
            stock_paid = h.stock(ADDON_ID)
            audit_after_pay = h.max_audit()
            sale_after_pay = h.sale_rows(order_id)
            osvc.update_order_status(order_id, to_status="MAKING", role="maker")
            done = osvc.update_order_status(order_id, to_status="COMPLETED", role="maker")
            rep.check("3.1 完成状态与完成时间", done["order"]["order_status"] == "COMPLETED"
                      and done["order"]["completed_at"] is not None)
            rep.check("3.2 完成不二次扣库", h.stock(ADDON_ID) == stock_paid, f"{stock_paid}")
            rep.check("3.3 完成不新增 SALE 流水", h.sale_rows(order_id) == sale_after_pay, f"{sale_after_pay} 条")
            rep.check("3.4 完成不产生审计行", h.max_audit() == audit_after_pay)

            rep.log("")
            rep.log("【4】重复完成由服务层拒绝")
            _expect_app_error(
                "4.1 重复完成 → AppError",
                lambda: osvc.update_order_status(order_id, to_status="COMPLETED", role="maker"),
                rep,
            )

            rep.log("")
            rep.log("【5】库存调整（usp_adjust_stock + 审计）")
            audit_before_adjust = h.max_audit()
            stock_before_adjust = h.stock("M0000002")
            isvc.adjust_stock("M0000002", Decimal("5"), STOCK_EMPLOYEE)
            rep.check("5.1 调整后库存 +5", h.stock("M0000002") == stock_before_adjust + 5,
                      f"{stock_before_adjust}→{h.stock('M0000002')}")
            rep.check(
                "5.2 写一条 GAIN 流水",
                int(h.scalar(
                    "SELECT COUNT(*) FROM dbo.inv_stock_log WHERE material_id='M0000002' "
                    "AND log_type='GAIN' AND qty=5 AND employee_id=?",
                    STOCK_EMPLOYEE,
                )) == 1,
            )
            adj_audits = h.audit_after(audit_before_adjust)
            rep.check(
                "5.3 审计记录调整员工与 +5 变动",
                len(adj_audits) == 1
                and adj_audits[0][5] == STOCK_EMPLOYEE
                and adj_audits[0][6] == "SESSION"
                and adj_audits[0][3] == Decimal("5.00"),
                str(adj_audits[:1]),
            )

            rep.log("")
            rep.log("【6】统计口径一致：usp_sale_statistics / fn_sales_summary / 窗口函数")
            h.cur.execute(
                "DECLARE @oc BIGINT, @sq BIGINT, @sa DECIMAL(38,2), @ra DECIMAL(38,2); "
                "EXEC dbo.usp_sale_statistics ?, ?, @oc OUTPUT, @sq OUTPUT, @sa OUTPUT, @ra OUTPUT; "
                "SELECT @oc, @sq, @sa, @ra",
                "2026-09-01", "2026-09-30",
            )
            # 过程先返回商品明细结果集，第二个结果集才是四个输出参数
            oc = sq = sa = ra = None
            while True:
                if h.cur.description:
                    rows = h.cur.fetchall()
                    if rows and len(rows[0]) == 4:
                        oc, sq, sa, ra = rows[0]
                        break
                if not h.cur.nextset():
                    raise AssertionError("usp_sale_statistics 未返回输出参数结果集")
            rep.check("6.0 过程返回订单数与退款额", oc is not None and ra is not None,
                      f"{oc} 单，退款 {ra}")
            f_sq, f_sa = h.one(
                "SELECT SUM(sold_qty), SUM(gross_amount) FROM dbo.fn_sales_summary(?, ?)",
                "2026-09-01", "2026-09-30",
            )
            r_sq, r_sa = h.one(
                "SELECT SUM(sold_qty), SUM(sales_amount) FROM dbo.fn_product_sales_rank(?, ?)",
                "2026-09-01", "2026-09-30",
            )
            addon_sum, discount_sum = h.one(
                "SELECT "
                "(SELECT SUM(a.qty * a.addon_price) FROM dbo.order_item_addon AS a "
                " JOIN dbo.shop_order AS o ON o.order_id = a.order_id "
                " WHERE o.order_status IN ('PAID','MAKING','COMPLETED') "
                "   AND CAST(o.created_at AS DATE) BETWEEN ? AND ?), "
                "(SELECT SUM(o.coupon_discount) FROM dbo.shop_order AS o "
                " WHERE o.order_status IN ('PAID','MAKING','COMPLETED') "
                "   AND CAST(o.created_at AS DATE) BETWEEN ? AND ?)",
                "2026-09-01", "2026-09-30", "2026-09-01", "2026-09-30",
            )
            rep.check("6.1 过程销量与表值函数一致", sq == f_sq, f"{sq} 件")
            # D3：过程销售额 = 券后实收 = 商品券前 + 加料 - 券优惠；表值函数只到商品券前
            rep.check(
                "6.2 过程销售额 = 商品 + 加料 - 优惠（D3 券后实收）",
                Decimal(str(sa)) == Decimal(str(f_sa)) + Decimal(str(addon_sum or 0)) - Decimal(str(discount_sum or 0)),
                f"{sa} = {f_sa} + {addon_sum} - {discount_sum}",
            )
            rep.check("6.3 排名函数合计与表值函数一致", (r_sq, r_sa) == (f_sq, f_sa),
                      f"{r_sq} 件 / {r_sa} 元")
            cum = h.one(
                "SELECT TOP 1 cumulative_qty, cumulative_amount FROM dbo.fn_daily_sales_cumulative(?, ?) "
                "ORDER BY sale_date DESC",
                "2026-09-01", "2026-09-30",
            )
            rep.check("6.4 累计函数末值与表值函数合计一致", tuple(cum) == (f_sq, f_sa), str(tuple(cum)))
            rep.check(
                "6.5 自然日骨架为 30 天（9 月整月）",
                int(h.scalar(
                    "SELECT COUNT(*) FROM dbo.fn_daily_sales_moving_avg(?, ?)",
                    "2026-09-01", "2026-09-30",
                )) == 30,
            )

            rep.log("")
            rep.log("【7】审计表与业务流水职责分离")
            rep.check(
                "7.1 审计行与该单 SALE 行数独立（审计按原料×事件，流水按原料×订单）",
                h.max_audit() > audit_before_pay and h.sale_rows(order_id) == len(usages),
            )

    except Exception:  # noqa: BLE001
        rep.failures += 1
        rep.log("")
        rep.log("EXCEPTION:")
        rep.log(traceback.format_exc())

    rep.log("")
    rep.log("【结论】")
    if rep.failures == 0:
        rep.log("PASS：应用链路、审计触发器、完成规则与窗口函数口径全部通过")
    else:
        rep.log(f"FAIL：{rep.failures} 项失败（见上）")
    rep.log(f"连接认证方式：{Config.DB_AUTH}；本专项使用单连接与保存点。")
    rep.log("=" * 96)

    text = "\n".join(rep.lines) + "\n"
    if "--write" in sys.argv:
        path = ROOT / "result" / "第11周-⑦应用回归.txt"
        path.write_text(text, encoding="utf-8")
        print("evidence:", path.relative_to(ROOT))
    return 0 if rep.failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
