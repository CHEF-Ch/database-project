-- ============================================================
-- 00-drop.sql —— 删库脚本（复现的起点）
-- 目标：把库删干净，使 01-schema / 02-seed 能从空库开始跑。
-- 这是一个**破坏性**脚本：执行后库中所有表与数据消失且不可撤销。
-- 本阶段（阶段一，1—4 周）无真实数据、一切均可由脚本重建，故删库无损失。
-- ============================================================

USE master;
GO

-- 第 1 步：库不存在就什么都不做，直接结束。
-- 使本脚本**可重复执行**：第一次删掉库，之后再跑不会报错。
IF DB_ID(N'BubbleTeaShop') IS NULL
BEGIN
    PRINT N'数据库 BubbleTeaShop 不存在，无需删除。';
    RETURN;
END
GO

-- 第 2 步：先断开所有占用连接，再删库。
-- 为什么必须先 SET SINGLE_USER：DROP DATABASE 要求无人连接。若此时 SSMS 里
-- 还开着连着该库的查询窗口，直接 DROP 会报"无法获得独占访问权"而失败。
-- WITH ROLLBACK IMMEDIATE 表示：踢掉其他会话并回滚其未提交事务，不等待它们自行断开。
-- 若省略 WITH ROLLBACK IMMEDIATE，SQL Server 会一直等（表现为脚本卡住）。
ALTER DATABASE BubbleTeaShop SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
GO

-- 第 3 步：删除数据库本体（含全部表、数据、日志文件）。
DROP DATABASE BubbleTeaShop;
GO

-- 自检：库应已不存在。DB_ID 返回 NULL 即删除成功。
-- 类型统一用 NVARCHAR：ISNULL 的返回类型取两个参数中优先级更高者（NVARCHAR > VARCHAR），
-- 此处含中文，两处都写 NVARCHAR 可避免隐式转换、也让列类型自洽。
SELECT
    ISNULL(CAST(DB_ID(N'BubbleTeaShop') AS NVARCHAR(10)), N'NULL（已删除）') AS 删除后DB_ID;
GO
