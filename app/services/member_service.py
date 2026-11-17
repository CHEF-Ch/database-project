"""Current customer's account, with identity supplied only by the session route."""
from app.dao import member
from app.dao.connection import db_cursor
from app.errors import AppError, ErrCode
from app.services.permissions import current_role


def get_account(member_id, *, points_offset=0, balance_offset=0):
    if current_role() != "customer":
        raise AppError(ErrCode.FORBIDDEN)
    if not member_id:
        raise AppError(ErrCode.BUSINESS_RULE, "当前账号未绑定会员，请联系店员。")
    with db_cursor("customer") as cur:
        account = member.get_member(cur, member_id)
        if account is None:
            raise AppError(ErrCode.NOT_FOUND)
        points = member.list_account_logs(cur, member_id, kind="points", offset=points_offset)
        balance = member.list_account_logs(cur, member_id, kind="balance", offset=balance_offset)
    return account, points, balance
