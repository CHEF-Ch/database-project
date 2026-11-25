/* ============================================================================
   sql/window_query.sql —— 第十一周 ④ 三类窗口查询（v2.3）
   ----------------------------------------------------------------------------
   W1 dbo.fn_product_sales_rank        商品销量/金额排名（并列用 RANK）
   W2 dbo.fn_daily_sales_cumulative    按自然日的累计销量/销售额（无销售日补 0）
   W3 dbo.fn_daily_sales_moving_avg    最近 3 个自然日移动平均（含 0 销售日）

   口径沿用第十周 D1/D2：时间字段 shop_order.created_at，闭区间含结束日；
   只算 PAID / MAKING / COMPLETED，REFUNDED 不计销量与销售额。
   自然日骨架用数字辅助表 + DATEADD 生成，不用递归 CTE（递归属第十二周）。

   函数是唯一口径来源；文件末尾三条 SELECT 是可直接改日期运行的演示查询，
   测试脚本 scripts/week11_window_test.sql 直接调用函数做手算对照。
   ============================================================================ */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

/* ---------------------------------------------------------------------------
   W1 商品销售排名
   ---------------------------------------------------------------------------
   排名指标：
     qty_rank    按销量降序，RANK()：并列同名次，后续名次跳号（1,1,3）；
     amount_rank 按销售额降序，DENSE_RANK()：并列同名次，后续名次不跳号（1,1,2）。
   粒度：商品 × 杯型（与 fn_sales_summary 同粒度）。
   --------------------------------------------------------------------------- */
CREATE OR ALTER FUNCTION dbo.fn_product_sales_rank
(
    @date_from DATE,
    @date_to   DATE
)
RETURNS TABLE
AS
RETURN
(
    SELECT p.product_id,
           p.name AS product_name,
           i.size,
           SUM(CAST(i.qty AS BIGINT)) AS sold_qty,
           SUM(i.qty * i.unit_price)  AS sales_amount,
           RANK()       OVER (ORDER BY SUM(CAST(i.qty AS BIGINT)) DESC) AS qty_rank,
           DENSE_RANK() OVER (ORDER BY SUM(i.qty * i.unit_price)  DESC) AS amount_rank
    FROM dbo.shop_order AS o
    JOIN dbo.order_item AS i ON i.order_id = o.order_id
    JOIN dbo.item_product AS p ON p.product_id = i.product_id
    WHERE CAST(o.created_at AS DATE) BETWEEN @date_from AND @date_to
      AND o.order_status IN ('PAID', 'MAKING', 'COMPLETED')
    GROUP BY p.product_id, p.name, i.size
);
GO

/* ---------------------------------------------------------------------------
   W2 按自然日累计销量 / 销售额
   ---------------------------------------------------------------------------
   输入粒度：自然日（含区间内没有销售的日子，补 0）。
   输出：sold_qty / sales_amount 当日值，cumulative_qty / cumulative_amount
        为从 @date_from 起按 sale_date 升序的累计值（ROWS UNBOUNDED PRECEDING）。
   --------------------------------------------------------------------------- */
CREATE OR ALTER FUNCTION dbo.fn_daily_sales_cumulative
(
    @date_from DATE,
    @date_to   DATE
)
RETURNS TABLE
AS
RETURN
(
    WITH digits AS
    (
        SELECT n FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS v(n)
    ),
    days AS
    (
        SELECT TOP (CASE WHEN @date_from IS NULL OR @date_to IS NULL OR @date_from > @date_to THEN 0
                         ELSE DATEDIFF(DAY, @date_from, @date_to) + 1 END)
               DATEADD(DAY, ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1, @date_from) AS sale_date
        FROM digits AS a CROSS JOIN digits AS b CROSS JOIN digits AS c
        CROSS JOIN digits AS d CROSS JOIN digits AS e
        CROSS JOIN digits AS f CROSS JOIN digits AS g
    ),
    daily AS
    (
        SELECT CAST(o.created_at AS DATE) AS sale_date,
               SUM(CAST(i.qty AS BIGINT)) AS sold_qty,
               SUM(i.qty * i.unit_price)  AS sales_amount
        FROM dbo.shop_order AS o
        JOIN dbo.order_item AS i ON i.order_id = o.order_id
        WHERE CAST(o.created_at AS DATE) BETWEEN @date_from AND @date_to
          AND o.order_status IN ('PAID', 'MAKING', 'COMPLETED')
        GROUP BY CAST(o.created_at AS DATE)
    ),
    filled AS
    (
        SELECT d.sale_date,
               ISNULL(x.sold_qty, 0)                       AS sold_qty,
               ISNULL(x.sales_amount, CAST(0 AS DECIMAL(38,2))) AS sales_amount
        FROM days AS d
        LEFT JOIN daily AS x ON x.sale_date = d.sale_date
    )
    SELECT sale_date,
           sold_qty,
           sales_amount,
           SUM(sold_qty)    OVER (ORDER BY sale_date ROWS UNBOUNDED PRECEDING) AS cumulative_qty,
           SUM(sales_amount) OVER (ORDER BY sale_date ROWS UNBOUNDED PRECEDING) AS cumulative_amount
    FROM filled
);
GO

/* ---------------------------------------------------------------------------
   W3 最近 3 个自然日移动平均
   ---------------------------------------------------------------------------
   窗口 = ROWS BETWEEN 2 PRECEDING AND CURRENT ROW（按 sale_date 升序）。
   自然日补 0 后开窗，所以「最近 3 天」是日历意义上的 3 天，不是最近 3 笔销售；
   区间开头不足 3 天时按实际已有天数求平均（第 1 天即当天值）。
   --------------------------------------------------------------------------- */
CREATE OR ALTER FUNCTION dbo.fn_daily_sales_moving_avg
(
    @date_from DATE,
    @date_to   DATE
)
RETURNS TABLE
AS
RETURN
(
    WITH digits AS
    (
        SELECT n FROM (VALUES (0),(1),(2),(3),(4),(5),(6),(7),(8),(9)) AS v(n)
    ),
    days AS
    (
        SELECT TOP (CASE WHEN @date_from IS NULL OR @date_to IS NULL OR @date_from > @date_to THEN 0
                         ELSE DATEDIFF(DAY, @date_from, @date_to) + 1 END)
               DATEADD(DAY, ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1, @date_from) AS sale_date
        FROM digits AS a CROSS JOIN digits AS b CROSS JOIN digits AS c
        CROSS JOIN digits AS d CROSS JOIN digits AS e
        CROSS JOIN digits AS f CROSS JOIN digits AS g
    ),
    daily AS
    (
        SELECT CAST(o.created_at AS DATE) AS sale_date,
               SUM(CAST(i.qty AS BIGINT)) AS sold_qty,
               SUM(i.qty * i.unit_price)  AS sales_amount
        FROM dbo.shop_order AS o
        JOIN dbo.order_item AS i ON i.order_id = o.order_id
        WHERE CAST(o.created_at AS DATE) BETWEEN @date_from AND @date_to
          AND o.order_status IN ('PAID', 'MAKING', 'COMPLETED')
        GROUP BY CAST(o.created_at AS DATE)
    ),
    filled AS
    (
        SELECT d.sale_date,
               ISNULL(x.sold_qty, 0)                            AS sold_qty,
               ISNULL(x.sales_amount, CAST(0 AS DECIMAL(38,2))) AS sales_amount
        FROM days AS d
        LEFT JOIN daily AS x ON x.sale_date = d.sale_date
    )
    SELECT sale_date,
           sold_qty,
           sales_amount,
           CAST(AVG(CAST(sold_qty AS DECIMAL(18,4)))
                OVER (ORDER BY sale_date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
                AS DECIMAL(18,4)) AS moving_avg_qty,
           CAST(AVG(CAST(sales_amount AS DECIMAL(18,4)))
                OVER (ORDER BY sale_date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
                AS DECIMAL(18,4)) AS moving_avg_amount
    FROM filled
);
GO

/* ---------------------------------------------------------------------------
   演示查询（可直接改日期运行；验收证据用 scripts/week11_window_test.sql）
   --------------------------------------------------------------------------- */
DECLARE @demo_from DATE = '2026-09-01',
        @demo_to   DATE = '2026-09-30';

PRINT N'--- W1 商品销量排名（qty_rank：RANK；amount_rank：DENSE_RANK）---';
SELECT product_id, product_name, size, sold_qty, sales_amount, qty_rank, amount_rank
FROM dbo.fn_product_sales_rank(@demo_from, @demo_to)
ORDER BY qty_rank, product_id, size;

PRINT N'--- W2 按自然日累计销量 / 销售额 ---';
SELECT sale_date, sold_qty, sales_amount, cumulative_qty, cumulative_amount
FROM dbo.fn_daily_sales_cumulative(@demo_from, @demo_to)
ORDER BY sale_date;

PRINT N'--- W3 最近 3 个自然日移动平均 ---';
SELECT sale_date, sold_qty, sales_amount, moving_avg_qty, moving_avg_amount
FROM dbo.fn_daily_sales_moving_avg(@demo_from, @demo_to)
ORDER BY sale_date;
GO
