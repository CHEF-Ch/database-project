-- ============================================================
-- 00-create.sql —— 建库脚本
-- 前提：库不存在（或已由 00-drop.sql 删除）。本脚本本身不删库。
-- 执行身份：需要 CREATE ANY DATABASE 权限（一般用 -E 集成认证的本机管理员账号即可）。
-- ============================================================

USE master;
GO

-- 第 1 步：若同名库已存在则**直接报错停止**，而不是静默跳过或删除。
-- 理由：① 静默跳过 → 后面的 01-schema 会在非空库上建表，"复现"变成"在旧库上追加"，结果不可信；
--       ② 自动删除 → 删库这么重的动作不该藏在"建库"脚本里，应由使用者显式触发（见 00-drop.sql）。
-- 这样写，使用者一眼就能从报错信息里知道该先做什么。
-- 用 RAISERROR 而非 THROW：RAISERROR 的 severity 显式可见（16 = 用户可修正错误，
-- 会令 sqlcmd -b 中断执行），且老版本即支持、教材常见；THROW 需额外解释错误号 50000。
-- 注意：RAISERROR 本身**不终止批**——severity 只影响客户端的 -b 行为，
--       服务器仍会继续执行后面的语句，故必须紧跟 RETURN。
IF DB_ID(N'BubbleTeaShop') IS NOT NULL
BEGIN
    RAISERROR(N'数据库 BubbleTeaShop 已存在。若要重新复现，请先执行 sql/00-drop.sql。', 16, 1);
    RETURN;
END
GO

-- 第 2 步：创建新库，并显式指定排序规则。
-- COLLATE Chinese_PRC_CI_AS：中文（PRC）排序规则；CI = Case Insensitive（比较不区分大小写，
-- 故 'P0000001' 与 'p0000001' 视为相等）；AS = Accent Sensitive（区分重音符号）。
-- 显式写出的必要性：实例默认排序规则因机器而异，若依赖默认值，换一台机器复现时
-- 字符串比较行为可能不同，导致候选码/查询结果出现差异。
CREATE DATABASE BubbleTeaShop
    COLLATE Chinese_PRC_CI_AS;
GO

-- 第 3 步：把后续脚本的执行上下文切到这个库。
-- 注意：CREATE DATABASE 与紧随其后的 USE 之间**必须有 GO**——CREATE DATABASE 在批的编译期解析，
-- 同批内的 USE 找不到刚建的库。
USE BubbleTeaShop;
GO

-- 自检：打印库与排序规则，确认建库结果符合预期。
-- 排序规则在这一层就能读到，不必等到建表；若与预期不符，这里就会显形。
SELECT
    DB_NAME()                                   AS 当前库,
    DATABASEPROPERTYEX(DB_NAME(), 'Collation')  AS 排序规则;
GO
