"""v1.0 阶段的 demo 账号（配置内用户存储）。

只存密码哈希，不存明文。明文仅用于演示，记录在 README 的"演示账号"表中。

更换账号/密码时用下面的命令生成哈希，替换 `pwd` 字段：

    python -c "from werkzeug.security import generate_password_hash as g; print(g('你的密码'))"

v2.0 阶段会改为 app_user 表（见 docs/09-应用开发/结构与运行.md）。
"""
from __future__ import annotations

# 演示账号：用户名 -> {pwd: 密码哈希, role: 应用角色, display: 展示名,
#                    member_id: 可选(顾客), employee_id: 可选(员工)}
DEMO_USERS: dict[str, dict] = {
    "clerk1": {
        "pwd": "scrypt:32768:8:1$1weoU1HEgeSfkerP$a3954ca8ad97f270386b10f86b94f625288f5f7644cc58e48bdf730afe0983cada19f74cd60ceb826c25dae1c6282e7a77b6444c259a0725e6c66370d3574ade",
        "role": "clerk",
        "display": "收银员-张伟",
        "employee_id": "E0000001",
    },
    "customer1": {
        "pwd": "scrypt:32768:8:1$u7eaXKs5Y4vhGmO7$b05e6993cf60aba1c0891e40693a4e2a35ddf78a1923edcdfe6f1b25df4b899fc3f40beeb643ca9d9d3226fe279993d46a06cbdf25d9f3af56faed5e094ce555",
        "role": "customer",
        "display": "顾客-李同学",
        "member_id": "C0000001",
    },
    "maker1": {
        "pwd": "scrypt:32768:8:1$2ZeJ9pmegcY2YqTT$b97399eee243bf41319b423b7a67590092c15c37cfcc97aaa013b168683de701b7d0c9f26225bee1cf2269d0489f1d5368a86bd50fcba1a75dd6a3c221029e0f",
        "role": "maker",
        "display": "制作员-王芳",
        "employee_id": "E0000002",
    },
    "stock1": {
        "pwd": "scrypt:32768:8:1$pg5Ye02EJgr26aGH$9db0d86d3365407a463c3f4148272dc89d4747d0830644463f4db0f68ef3426500d540d7d09be8c0243ccec933324b926f4db0ce1544d9656f8eb8f0f6912007",
        "role": "stock",
        "display": "库存管理员-赵强",
        "employee_id": "E0000003",
    },
    "manager1": {
        "pwd": "scrypt:32768:8:1$xKNHKxGAyKagysXA$de94edb43d822ae469c555f942277a23640eca818e0d771defe0810fe1a71ae96ff7c3ca2520e4719092959c9e1da1379140b1edb9fbdf979f5fe5fe6d9404d6",
        "role": "manager",
        "display": "店长-陈明",
        "employee_id": "E0000004",
    },
}

# 明文密码只写在 README 与 docs/09 §7 的演示账号表里，代码中只留哈希。
