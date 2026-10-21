/* ============================================================================
   第三周 步骤 ③ 方向 B —— 复核 / 回归测试脚本
   ----------------------------------------------------------------------------
   对象：Chen 在 1171956 中对 sql/01-schema.sql 的改动
         · 新增 9 条 CHECK
         · item_material.unit 由 VARCHAR(10) 改为 NVARCHAR(10)
   复核人：Chen
   口径：从运行库反向验证约束是否真的在拦；
         每条用例都在事务内执行并 ROLLBACK，库数据不会被改动。
   运行环境：SQL Server（本机 .\SQLEXPRESS），库 MilkTeaShop（v0.1）
   运行方式：
       sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i scripts/week3_review_b.sql
   期望结果：21/21 PASS，末行 ALL TESTS PASSED
   ============================================================================ */
USE [MilkTeaShop];
GO

SET NOCOUNT ON;
SET XACT_ABORT OFF;
-- shop_order 上有筛选唯一索引，DML 时必须显式打开这两个 SET 选项；
-- sqlcmd 默认 QUOTED_IDENTIFIER=OFF，否则会先报 1934 而不是 CHECK 的 547。
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;

IF OBJECT_ID('tempdb..#cases') IS NOT NULL DROP TABLE #cases;
IF OBJECT_ID('tempdb..#r') IS NOT NULL DROP TABLE #r;

CREATE TABLE #cases (id varchar(10), item nvarchar(200), expect int, stmt nvarchar(max));
CREATE TABLE #r (id varchar(10), item nvarchar(200), expect int, actual int, ok bit, msg nvarchar(400));

INSERT #cases (id, item, expect, stmt) VALUES
-- B1：9 条新增 CHECK 逐条负例（期望 547）
('B1-1', N'CK_item_material_reserved_le_stock：reserved_qty > stock_qty', 547,
 N'UPDATE TOP (1) dbo.item_material SET reserved_qty = stock_qty + 1;'),
('B1-2', N'CK_inv_restock_received：RECEIVED 但 received_at 为 NULL', 547,
 N'UPDATE TOP (1) dbo.inv_restock SET restock_status = ''RECEIVED'', received_at = NULL;'),
('B1-3', N'CK_member_point_log_order：EARN_SALE 但 order_id 为 NULL', 547,
 N'UPDATE TOP (1) dbo.member_point_log SET point_type = ''EARN_SALE'', order_id = NULL;'),
('B1-4', N'CK_mkt_coupon_rule_threshold：threshold_amount = 0', 547,
 N'UPDATE TOP (1) dbo.mkt_coupon_rule SET threshold_amount = 0;'),
('B1-5', N'CK_mkt_coupon_rule_discount：discount_amount > threshold_amount', 547,
 N'UPDATE TOP (1) dbo.mkt_coupon_rule SET discount_amount = threshold_amount + 1;'),
('B1-6', N'CK_mkt_coupon_rule_valid：valid_to < valid_from', 547,
 N'UPDATE TOP (1) dbo.mkt_coupon_rule SET valid_to = DATEADD(day, -1, valid_from);'),
('B1-7', N'CK_shop_order_total_amount：total_amount = -1', 547,
 N'UPDATE TOP (1) dbo.shop_order SET total_amount = -1;'),
('B1-8', N'CK_order_platform_delivery_fee：delivery_fee = -1', 547,
 N'UPDATE TOP (1) dbo.order_platform SET delivery_fee = -1;'),
('B1-9', N'CK_item_material_unit：unit = N''千克''', 547,
 N'UPDATE TOP (1) dbo.item_material SET unit = N''千克'';'),
-- B2：unit 的三个合法取值（期望放行）
('B2-1', N'unit = N''克'' 应放行', 0,
 N'UPDATE TOP (1) dbo.item_material SET unit = N''克'';'),
('B2-2', N'unit = N''毫升'' 应放行', 0,
 N'UPDATE TOP (1) dbo.item_material SET unit = N''毫升'';'),
('B2-3', N'unit = N''个'' 应放行', 0,
 N'UPDATE TOP (1) dbo.item_material SET unit = N''个'';'),
-- B3：免单券边界 discount == threshold（期望放行）
('B3', N'CK_mkt_coupon_rule_discount：discount_amount = threshold_amount 应放行', 0,
 N'UPDATE TOP (1) dbo.mkt_coupon_rule SET discount_amount = threshold_amount;'),
-- B4a：CK_inv_restock_received 的四组取值
('B4a-1', N'received_at=NULL + RECEIVED 应拒', 547,
 N'UPDATE TOP (1) dbo.inv_restock SET restock_status = ''RECEIVED'', received_at = NULL;'),
('B4a-2', N'received_at=非NULL + PENDING 应拒', 547,
 N'UPDATE TOP (1) dbo.inv_restock SET restock_status = ''PENDING'', received_at = SYSDATETIME();'),
('B4a-3', N'received_at=非NULL + RECEIVED 应放行', 0,
 N'UPDATE TOP (1) dbo.inv_restock SET restock_status = ''RECEIVED'', received_at = SYSDATETIME();'),
('B4a-4', N'received_at=NULL + PENDING 应放行', 0,
 N'UPDATE TOP (1) dbo.inv_restock SET restock_status = ''PENDING'', received_at = NULL;'),
-- B4b：CK_member_point_log_order 的四组取值
('B4b-1', N'order_id=非NULL + EARN_RECHARGE 应拒', 547,
 N'UPDATE TOP (1) dbo.member_point_log SET point_type = ''EARN_RECHARGE'', order_id = (SELECT TOP 1 order_id FROM dbo.shop_order ORDER BY order_id);'),
('B4b-2', N'order_id=NULL + EARN_SALE 应拒', 547,
 N'UPDATE TOP (1) dbo.member_point_log SET point_type = ''EARN_SALE'', order_id = NULL;'),
('B4b-3', N'order_id=NULL + EARN_RECHARGE 应放行', 0,
 N'UPDATE TOP (1) dbo.member_point_log SET point_type = ''EARN_RECHARGE'', order_id = NULL;'),
('B4b-4', N'order_id=非NULL + EARN_SALE 应放行', 0,
 N'UPDATE TOP (1) dbo.member_point_log SET point_type = ''EARN_SALE'', order_id = (SELECT TOP 1 order_id FROM dbo.shop_order ORDER BY order_id);');

DECLARE @id varchar(10), @item nvarchar(200), @expect int, @stmt nvarchar(max);
DECLARE @err int, @msg nvarchar(4000);
DECLARE cur CURSOR LOCAL FAST_FORWARD FOR SELECT id, item, expect, stmt FROM #cases ORDER BY id;
OPEN cur;
FETCH NEXT FROM cur INTO @id, @item, @expect, @stmt;
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @err = NULL; SET @msg = N'';
    BEGIN TRY
        BEGIN TRAN;
        EXEC sp_executesql @stmt;
        SET @err = 0;
        ROLLBACK;
    END TRY
    BEGIN CATCH
        SET @err = ERROR_NUMBER();
        SET @msg = ERROR_MESSAGE();
        IF @@TRANCOUNT > 0 ROLLBACK;
    END CATCH
    INSERT #r (id, item, expect, actual, ok, msg)
    VALUES (@id, @item, @expect, @err, CASE WHEN @err = @expect THEN 1 ELSE 0 END, @msg);
    FETCH NEXT FROM cur INTO @id, @item, @expect, @stmt;
END
CLOSE cur; DEALLOCATE cur;

SELECT id, item, expect, actual, CASE WHEN ok = 1 THEN 'PASS' ELSE 'FAIL' END AS result
FROM #r ORDER BY id;

-- B2 类型核对：unit 必须是 NVARCHAR(10)（max_length 为字节数，20/2 = 10 字符）
SELECT 'B2-type' AS item, t.name AS type_name, c.max_length / 2 AS len_chars, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON c.user_type_id = t.user_type_id
WHERE c.object_id = OBJECT_ID('dbo.item_material') AND c.name = 'unit';

-- B5 运行库 CHECK 总数：应为 45（基线 36 + 新增 9）
SELECT 'B5-count' AS item, COUNT(*) AS check_count FROM sys.check_constraints;

DECLARE @total int = (SELECT COUNT(*) FROM #r);
DECLARE @pass int = (SELECT COUNT(*) FROM #r WHERE ok = 1);
SELECT @total AS total, @pass AS passed, @total - @pass AS failed,
       CASE WHEN @pass = @total THEN 'ALL TESTS PASSED' ELSE 'FAILED' END AS result;
GO
