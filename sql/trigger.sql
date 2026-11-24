/* ============================================================================
   sql/trigger.sql —— 第十一周 ②③ 触发器（v2.3）
   ----------------------------------------------------------------------------
   ① trg_item_material_audit   库存审计：记录 item_material 现存量变动前后值
   ② trg_shop_order_complete   订单完成规则：只有合法且已扣料的 MAKING 单能完成

   定稿依据：docs/10-数据库编程/触发器与窗口查询设计.md（第 11 周 ①）。
   部署方式：sql/migration_v2_3_audit.sql 内用 :r 引入本文件；也可单独重跑
   （均为 CREATE OR ALTER，幂等）。

   两条硬约束（对应 R05「审计与业务流水分离」）：
   · 本文件不写 inv_stock_log —— 业务流水仍由 DAO / usp_adjust_stock 唯一写入；
   · 订单完成触发器不更新 item_material —— 支付时已经扣料，完成不得再扣一次。
   ============================================================================ */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

/* ---------------------------------------------------------------------------
   ② 库存审计触发器
   ---------------------------------------------------------------------------
   事件：INSERT / UPDATE / DELETE on dbo.item_material
   记录：INSERT（0 → stock_qty）、DELETE（stock_qty → 0）、
         UPDATE 仅当 stock_qty 变化（reserved_qty 的预占/释放不记）。
   操作人：SESSION_CONTEXT(N'employee_id') 且命中 staff_employee → operator_source='SESSION'；
           否则 SUSER_SNAME() 兜底 → operator_source='LOGIN'（不阻断写入）。
   多行：按 inserted/deleted 集合插入，N 行变动写 N 条审计。
   --------------------------------------------------------------------------- */
CREATE OR ALTER TRIGGER dbo.trg_item_material_audit
ON dbo.item_material
AFTER INSERT, UPDATE, DELETE
AS
BEGIN
    SET NOCOUNT ON;

    -- 仅当确实存在需要审计的现存量变化时才进入写入。
    -- 这段提前返回同时保护 role_customer：它只更新 reserved_qty，不应需要审计表的 INSERT 权限。
    IF NOT EXISTS (
            SELECT 1
            FROM inserted AS i
            LEFT JOIN deleted AS d ON d.material_id = i.material_id
            WHERE d.material_id IS NULL OR i.stock_qty <> d.stock_qty
        )
       AND NOT EXISTS (
            SELECT 1
            FROM deleted AS d
            LEFT JOIN inserted AS i ON i.material_id = d.material_id
            WHERE i.material_id IS NULL
        )
        RETURN;

    DECLARE @operator_id VARCHAR(128) = NULL,
            @operator_source VARCHAR(10) = 'LOGIN',
            @login_name NVARCHAR(128) = SUSER_SNAME();

    BEGIN TRY
        SET @operator_id = CONVERT(VARCHAR(128), SESSION_CONTEXT(N'employee_id'));
    END TRY
    BEGIN CATCH
        -- 会话上下文不可用/类型不可转换：按 LOGIN 来源记，不能因此阻断库存写入。
        SET @operator_id = NULL;
    END CATCH;

    IF @operator_id IS NOT NULL
    BEGIN
        SET @operator_id = LTRIM(RTRIM(@operator_id));
        IF @operator_id = ''
           OR NOT EXISTS (SELECT 1 FROM dbo.staff_employee WHERE employee_id = @operator_id)
            SET @operator_id = NULL;
        ELSE
            SET @operator_source = 'SESSION';
    END;

    INSERT dbo.inv_stock_audit
        (material_id, before_qty, after_qty, change_type,
         operator_id, operator_source, login_name, changed_at)
    SELECT x.material_id, x.before_qty, x.after_qty, x.change_type,
           @operator_id, @operator_source, @login_name, SYSDATETIME()
    FROM (
        -- 新增原料：视为从 0 建立现存量
        SELECT i.material_id,
               CAST(0 AS DECIMAL(10,2)) AS before_qty,
               i.stock_qty              AS after_qty,
               CAST('INSERT' AS VARCHAR(10)) AS change_type
        FROM inserted AS i
        WHERE NOT EXISTS (SELECT 1 FROM deleted AS d WHERE d.material_id = i.material_id)

        UNION ALL

        -- 删除原料：审计保留删除前现存量（不因主数据删除丢历史）
        SELECT d.material_id,
               d.stock_qty,
               CAST(0 AS DECIMAL(10,2)),
               CAST('DELETE' AS VARCHAR(10))
        FROM deleted AS d
        WHERE NOT EXISTS (SELECT 1 FROM inserted AS i WHERE i.material_id = d.material_id)

        UNION ALL

        -- 修改：只有 stock_qty 变化才记；reserved_qty 单独变化不产生审计行
        SELECT i.material_id,
               d.stock_qty,
               i.stock_qty,
               CAST('UPDATE' AS VARCHAR(10))
        FROM inserted AS i
        JOIN deleted AS d ON d.material_id = i.material_id
        WHERE i.stock_qty <> d.stock_qty
    ) AS x;
END;
GO

/* ---------------------------------------------------------------------------
   ③ 订单完成规则触发器
   ---------------------------------------------------------------------------
   事件：AFTER UPDATE on dbo.shop_order，仅当 order_status 出现在 SET 列表。
   规则（错误号）：
     C1 51112  只允许 MAKING → COMPLETED
     C2 51111  已是 COMPLETED 又写 COMPLETED：重复完成/改已完成单
     C3 51113  completed_at 必填、paid_at 必填、completed_at >= paid_at
     C4 51114  至少一条明细；51115 明细数量/单价非法
     C5 51116  金额等式：total = subtotal - discount；subtotal = 商品 + 加料快照
     C6 51117  必须已有 SALE 扣料流水；有耗用快照时逐原料核对 SALE 数量
   多行：全部用 inserted/deleted 集合判断；任一非法行 THROW → 整条语句回滚。
   不做：不修改 item_material（支付已扣料）、不比较商品当前杯型/名称（历史快照优先）。
   --------------------------------------------------------------------------- */
CREATE OR ALTER TRIGGER dbo.trg_shop_order_complete
ON dbo.shop_order
AFTER UPDATE
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT UPDATE(order_status)
        RETURN;

    -- C2：重复完成（对已完成单再次完成/重写完成时间）；纯 no-op（SET order_status=order_status）放行，
    -- 以免权限矩阵里“制作员改 order_status 列”这类空更新被误判为重复完成。
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'COMPLETED'
          AND (i.completed_at IS NULL
               OR d.completed_at IS NULL
               OR i.completed_at <> d.completed_at)
    )
        THROW 51111, N'订单已完成，不能重复完成或重写完成时间。', 1;

    -- 没有「转为 COMPLETED」的行：其它状态流转不归本触发器管
    IF NOT EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status <> 'COMPLETED'
    )
        RETURN;

    -- C1：只允许 MAKING → COMPLETED（PAID 必须先进入 MAKING）
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status <> 'COMPLETED'
          AND d.order_status <> 'MAKING'
    )
        THROW 51112, N'只有「制作中」的订单可以完成；非法状态不能直接转为已完成。', 1;

    -- C3：完成时间与支付时间
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'MAKING'
          AND (i.completed_at IS NULL
               OR i.paid_at IS NULL
               OR i.completed_at < i.paid_at)
    )
        THROW 51113, N'完成时间缺失或早于支付时间，订单不能完成。', 1;

    -- C4：至少一条明细
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'MAKING'
          AND NOT EXISTS (
              SELECT 1 FROM dbo.order_item AS oi WHERE oi.order_id = i.order_id
          )
    )
        THROW 51114, N'订单没有任何明细，不能完成。', 1;

    -- C4：明细数量/单价非法（数据库已有 CHECK，这里再挡一次并给出订单号级提示）
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        JOIN dbo.order_item AS oi ON oi.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'MAKING'
          AND (oi.qty <= 0 OR oi.unit_price < 0)
    )
        THROW 51115, N'订单明细存在非法数量或单价，不能完成。', 1;

    -- C5：金额等式（含加料成交价快照，不读当前加料价）
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        OUTER APPLY (
            SELECT SUM(oi.qty * oi.unit_price) AS goods
            FROM dbo.order_item AS oi
            WHERE oi.order_id = i.order_id
        ) AS g
        OUTER APPLY (
            SELECT SUM(a.qty * a.addon_price) AS addons
            FROM dbo.order_item_addon AS a
            WHERE a.order_id = i.order_id
        ) AS ad
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'MAKING'
          AND (i.total_amount <> i.item_subtotal - i.coupon_discount
               OR i.item_subtotal <> ISNULL(g.goods, 0) + ISNULL(ad.addons, 0))
    )
        THROW 51116, N'订单金额与明细/加料快照不一致，不能完成。', 1;

    -- C6：必须已有支付扣料证据（SALE 流水）
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'MAKING'
          AND NOT EXISTS (
              SELECT 1
              FROM dbo.inv_stock_log AS l
              WHERE l.order_id = i.order_id
                AND l.log_type = 'SALE'
          )
    )
        THROW 51117, N'该订单没有支付扣料流水（SALE），不能完成；请先核对支付与库存。', 1;

    -- C6：有耗用快照时，逐原料核对 SALE 数量与快照一致（防止少扣/漏扣后仍完成）
    IF EXISTS (
        SELECT 1
        FROM inserted AS i
        JOIN deleted  AS d ON d.order_id = i.order_id
        JOIN dbo.order_material_usage AS u ON u.order_id = i.order_id
        WHERE i.order_status = 'COMPLETED'
          AND d.order_status = 'MAKING'
          AND NOT EXISTS (
              SELECT 1
              FROM dbo.inv_stock_log AS l
              WHERE l.order_id = u.order_id
                AND l.material_id = u.material_id
                AND l.log_type = 'SALE'
                AND l.qty = u.qty
          )
    )
        THROW 51117, N'订单耗用快照与 SALE 扣料流水不一致，不能完成。', 1;
END;
GO
