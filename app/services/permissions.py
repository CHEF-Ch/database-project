"""权限矩阵与访问控制（⑥ 的页面/接口校验，⑦ 的测试清单共用同一份数据）。

注意：页面隐藏按钮只是体验控制；真正的校验在这里（后端）执行一次，
数据库层再由各角色对应的受限 SQL 登录挡一次。
"""
from __future__ import annotations

from functools import wraps

from flask import session

from app.errors import AppError, ErrCode

ROLE_LABELS: dict[str, str] = {
    "clerk": "店员（收银）",
    "customer": "顾客",
    "maker": "制作员",
    "stock": "库存管理员",
    "manager": "店长",
}

# 权限点 → 各角色是否可用（唯一事实来源）
PERMISSIONS: dict[str, set[str]] = {
    "clerk": {
        "product.read",
        "inventory.read",
        "order.create",
        "order.read",
        "order.read_own",
    },
    "customer": {
        "product.read",
        "order.create",        # 自助点单
        "order.read_own",
    },
    "maker": {
        "product.read",
        "order.read",
        "order.update_status",
    },
    "stock": {
        "product.read",
        "inventory.read",
        "inventory.adjust",
        "restock.create",
    },
    "manager": {
        "product.read",
        "product.write",
        "inventory.read",
        "order.read",
        "order.refund",
        "coupon.write",
    },
}


def current_role() -> str | None:
    return session.get("role")


def can(permission: str) -> bool:
    role = current_role()
    return bool(role) and permission in PERMISSIONS.get(role, set())


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_role():
            raise AppError(ErrCode.AUTH_REQUIRED)
        return view(*args, **kwargs)

    return wrapper


def role_required(permission: str):
    """要求已登录且具备指定权限点。"""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not can(permission):
                raise AppError(
                    ErrCode.FORBIDDEN,
                    detail=f"role={current_role()} lacks {permission}",
                )
            return view(*args, **kwargs)

        return wrapper

    return decorator


def role_required_any(*permissions: str):
    """要求已登录且具备其中**任一**权限点。

    用于“同一入口、不同角色不同视野”的页面，例如订单详情：店员/制作员/店长凭
    ``order.read`` 看全量，顾客凭 ``order.read_own`` 只能看自己的单——行级过滤仍由
    业务层按 session 的 ``member_id`` 做（``order_service.get_order_detail``）。
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            role = current_role()
            if not (PERMISSIONS.get(role or "", set()) & set(permissions)):
                raise AppError(
                    ErrCode.FORBIDDEN,
                    detail=f"role={role} lacks any of {permissions}",
                )
            return view(*args, **kwargs)

        return wrapper

    return decorator
