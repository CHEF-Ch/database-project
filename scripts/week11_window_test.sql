/* ============================================================================
   scripts/week11_window_test.sql —— 第十一周 ⑥ 窗口查询验证
   ----------------------------------------------------------------------------
   固定样例（2099-01-01 至 2099-01-05，独立日期段，不碰 seed）：
     01-01  P0000001 ×2 @10 = 20；P0000002 ×1 @5 = 5        日销 3 件 / 25 元
     01-02  P0000001 ×3 @10 = 30                              日销 3 件 / 30 元
     01-03  无销售                                            日销 0 件 / 0 元
     01-04  P0000002 ×2 @5 = 10；P0000003 ×3 @10 = 30         日销 5 件 / 40 元
     01-05  无销售                                            日销 0 件 / 0 元
   另放两张干扰单：01-02 REFUNDED 99 杯、01-03 PENDING 50 杯，都不应计入。

   手算预期：
     商品合计  P0000001 5 件 / 50 元；P0000002 3 件 / 15 元；P0000003 3 件 / 30 元
     销量排名  P1=1；P2=P3=2（RANK 并列跳号）；金额排名 P1=1、P3=2、P2=3
     累计      3/25 → 6/55 → 6/55 → 11/95 → 11/95
     3 日移动平均（自然日，含 0）  件：3、3、2、2.6667、1.6667
                                  额：25、27.5、18.3333、23.3333、13.3333

   运行：
     sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -d MilkTeaShop_week11_x -i scripts/week11_window_test.sql
   ============================================================================ */
:ON ERROR EXIT
SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
SET XACT_ABORT OFF;

IF DB_NAME() IN (N'master', N'model', N'msdb', N'tempdb', N'MilkTeaShop', N'MilkTeaShop_v01')
    THROW 51950, N'请对第十一周验收库运行本测试，勿对源库执行。', 1;

IF OBJECT_ID('dbo.fn_product_sales_rank', 'IF') IS NULL
   OR OBJECT_ID('dbo.fn_daily_sales_cumulative', 'IF') IS NULL
   OR OBJECT_ID('dbo.fn_daily_sales_moving_avg', 'IF') IS NULL
    THROW 51951, N'窗口查询函数未部署：请先执行 sql/migration_v2_3_audit.sql。', 1;
GO

CREATE PROCEDURE #w11_sale
    @order_id CHAR(12),
    @day      DATE,
    @product  CHAR(8),
    @qty      INT,
    @price    DECIMAL(10,2),
    @status   VARCHAR(20) = 'PAID'
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @amount DECIMAL(10,2) = @qty * @price;
    INSERT dbo.shop_order
        (order_id, channel, order_mode, order_status, member_id, employee_id,
         item_subtotal, coupon_discount, total_amount, pay_method, pay_txn_no,
         created_at, paid_at, completed_at, refund_amount, refund_at)
    VALUES
        (@order_id, 'COUNTER', 'COUNTER_CASHIER', @status, NULL, 'E0000001',
         @amount, 0, @amount,
         CASE WHEN @status = 'PENDING' THEN NULL ELSE 'CASH' END, NULL,
         CAST(@day AS DATETIME2(0)),
         CASE WHEN @status = 'PENDING' THEN NULL ELSE CAST(@day AS DATETIME2(0)) END,
         NULL, NULL, NULL);
    INSERT dbo.order_item
        (order_id, item_line, product_id, size, sugar_level, ice_level, temp_level, qty, unit_price)
    VALUES (@order_id, 1, @product, 'MEDIUM', 'FULL', 'REGULAR', 'COLD', @qty, @price);
    IF @status = 'REFUNDED'
        UPDATE dbo.shop_order SET refund_amount = @amount, refund_at = CAST(@day AS DATETIME2(0))
        WHERE order_id = @order_id;
END;
GO

PRINT N'== 0. 夹具准备（事务内，结束回滚） ==';
BEGIN TRANSACTION;

    EXEC #w11_sale '209901010001', '2099-01-01', 'P0000001', 2, 10.00;
    EXEC #w11_sale '209901010002', '2099-01-01', 'P0000002', 1, 5.00;
    EXEC #w11_sale '209901010003', '2099-01-02', 'P0000001', 3, 10.00;
    EXEC #w11_sale '209901010004', '2099-01-04', 'P0000002', 2, 5.00;
    EXEC #w11_sale '209901010005', '2099-01-04', 'P0000003', 3, 10.00;
    -- 干扰单
    EXEC #w11_sale '209901010006', '2099-01-02', 'P0000001', 99, 10.00, 'REFUNDED';
    EXEC #w11_sale '209901010007', '2099-01-03', 'P0000001', 50, 10.00, 'PENDING';
PRINT N'PASS 0 夹具 5 张有效单 + REFUNDED/PENDING 干扰单';

/* ---------------------------------------------------------------------------
   W1 商品销量排名
   --------------------------------------------------------------------------- */
PRINT N'== 1. W1 商品排名：并列与口径 ==';
IF (SELECT COUNT(*) FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')) <> 3
    THROW 51952, N'W1 应只返回 3 个商品（排除 REFUNDED/PENDING 与干扰单）。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')
    WHERE product_id = 'P0000001' AND sold_qty = 5 AND sales_amount = 50.00
      AND qty_rank = 1 AND amount_rank = 1
)
    THROW 51953, N'P0000001 应为 5 件/50 元、双榜第 1。', 1;
IF (SELECT COUNT(*) FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')
    WHERE product_id IN ('P0000002', 'P0000003') AND sold_qty = 3 AND qty_rank = 2) <> 2
    THROW 51954, N'P0000002/P0000003 应并列销量第 2（RANK 跳号）。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')
    WHERE product_id = 'P0000003' AND amount_rank = 2
)
    THROW 51955, N'P0000003 金额 30 应排第 2（DENSE_RANK）。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')
    WHERE product_id = 'P0000002' AND amount_rank = 3
)
    THROW 51956, N'P0000002 金额 15 应排第 3。', 1;
PRINT N'PASS 1 W1：销量/金额排名、并列与 REFUNDED/PENDING 排除均正确';

/* ---------------------------------------------------------------------------
   W2 按自然日累计
   --------------------------------------------------------------------------- */
PRINT N'== 2. W2 自然日累计（含无销售日补 0） ==';
IF (SELECT COUNT(*) FROM dbo.fn_daily_sales_cumulative('2099-01-01', '2099-01-05')) <> 5
    THROW 51957, N'W2 应返回 5 个自然日（含无销售日）。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_cumulative('2099-01-01', '2099-01-05')
    WHERE sale_date = '2099-01-03' AND sold_qty = 0 AND sales_amount = 0
      AND cumulative_qty = 6 AND cumulative_amount = 55
)
    THROW 51958, N'01-03 应为 0 销售、累计 6 件/55 元（累计不因 0 销售日回退）。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_cumulative('2099-01-01', '2099-01-05')
    WHERE sale_date = '2099-01-05' AND cumulative_qty = 11 AND cumulative_amount = 95
)
    THROW 51959, N'01-05 累计应为 11 件/95 元。', 1;
PRINT N'PASS 2 W2：5 个自然日、无销售日补 0、累计值手算一致';

/* ---------------------------------------------------------------------------
   W3 最近 3 个自然日移动平均
   --------------------------------------------------------------------------- */
PRINT N'== 3. W3 3 日移动平均（自然日窗口） ==';
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_moving_avg('2099-01-01', '2099-01-05')
    WHERE sale_date = '2099-01-02' AND ABS(moving_avg_qty - 3.0) < 0.0001
)
    THROW 51960, N'01-02 移动平均应为 (3+3)/2 = 3。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_moving_avg('2099-01-01', '2099-01-05')
    WHERE sale_date = '2099-01-03' AND ABS(moving_avg_qty - 2.0) < 0.0001
)
    THROW 51961, N'01-03 移动平均应为 (3+3+0)/3 = 2（无销售日按 0 计入）。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_moving_avg('2099-01-01', '2099-01-05')
    WHERE sale_date = '2099-01-04' AND ABS(moving_avg_qty - 2.6667) < 0.0001
      AND ABS(moving_avg_amount - 23.3333) < 0.0001
)
    THROW 51962, N'01-04 移动平均应为 (3+0+5)/3 = 2.6667、金额 23.3333。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_moving_avg('2099-01-01', '2099-01-05')
    WHERE sale_date = '2099-01-05' AND ABS(moving_avg_qty - 1.6667) < 0.0001
)
    THROW 51963, N'01-05 移动平均应为 (0+5+0)/3 = 1.6667（旧数据滑出窗口）。', 1;
PRINT N'PASS 3 W3：3 日窗口、含 0 销售日、窗口滑动后旧值退出';

/* ---------------------------------------------------------------------------
   边界与跨函数一致性
   --------------------------------------------------------------------------- */
PRINT N'== 4. 边界：起止同日 / 非法区间 ==';
IF (SELECT COUNT(*) FROM dbo.fn_daily_sales_cumulative('2099-01-04', '2099-01-04')) <> 1
    THROW 51964, N'起止同日应只返回 1 行。', 1;
IF NOT EXISTS (
    SELECT 1 FROM dbo.fn_daily_sales_cumulative('2099-01-04', '2099-01-04')
    WHERE sale_date = '2099-01-04' AND sold_qty = 5 AND cumulative_qty = 5
)
    THROW 51965, N'起止同日应包含当日销售 5 件。', 1;
IF EXISTS (SELECT 1 FROM dbo.fn_daily_sales_cumulative('2099-01-05', '2099-01-01'))
    THROW 51966, N'非法区间（起 > 止）应返回空结果集。', 1;
IF EXISTS (SELECT 1 FROM dbo.fn_daily_sales_moving_avg('2099-01-05', '2099-01-01'))
    THROW 51967, N'W3 非法区间应返回空结果集。', 1;
PRINT N'PASS 4 边界：起止同日含当日；起 > 止返回空';

IF EXISTS (SELECT 1 FROM dbo.fn_daily_sales_cumulative(NULL, '2099-01-05'))
   OR EXISTS (SELECT 1 FROM dbo.fn_daily_sales_moving_avg('2099-01-01', NULL))
    THROW 51970, N'日期参数为 NULL 时应返回空。', 1;

PRINT N'== 5. 与第十周 fn_sales_summary 口径一致 ==';
IF (SELECT COALESCE(SUM(sold_qty), 0) FROM dbo.fn_sales_summary('2099-01-01', '2099-01-05')) <> 11
   OR (SELECT COALESCE(SUM(gross_amount), 0) FROM dbo.fn_sales_summary('2099-01-01', '2099-01-05')) <> 95
    THROW 51968, N'窗口函数与 fn_sales_summary 合计不一致。', 1;
IF (SELECT COALESCE(SUM(sold_qty), 0) FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')) <> 11
   OR (SELECT COALESCE(SUM(sales_amount), 0) FROM dbo.fn_product_sales_rank('2099-01-01', '2099-01-05')) <> 95
    THROW 51969, N'W1 合计与 fn_sales_summary 不一致。', 1;
PRINT N'PASS 5 W1/W2/W3 与 fn_sales_summary 同为 11 件 / 95 元';

ROLLBACK TRANSACTION;
PRINT N'';
PRINT N'ALL WINDOW TESTS PASSED（5 组：排名 / 累计 / 移动平均 / 边界 / 跨函数一致）';
