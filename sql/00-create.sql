/* ============================================================================
   00-create.sql —— 建库（先删同名库，再重建）
   ----------------------------------------------------------------------------
   用途：把实例恢复成"空库起点"，供 01-schema.sql / constraint.sql / 02-seed.sql
         在其上执行。这三个脚本都只能在空库上跑，故本脚本必须每次都把库清空。

   复现口径（见 sql/README.md）：复现 = 把整套脚本从头再跑一次，得到相同的
   表结构与样例数据；不是在已有库上"不报错地再跑一遍"。

   ★ 小组协作须知（共享实例时这三条最要紧）★
   1. 本脚本会【真的删除】MilkTeaShop 库及其全部对象与数据。阶段一不涉及真实
      数据，删库无损失；若不删，第二次复现必然因"对象已存在"而失败。
   2. 删库会【强制断开】一切连着该库的会话，并回滚其未提交事务
      （SET SINGLE_USER WITH ROLLBACK IMMEDIATE）。
      最常见的占用者其实是【自己】：SSMS 的"对象资源管理器"只要展开过该库，就一直
      握着连接，于是自己跑复现反而撞上 "Cannot drop database ... currently in use"。
      跑之前先关掉自己的 SSMS 查询窗口 / 断开对象资源管理器。
      只有在全组共用【同一个实例】（实验室机器、内网或云上的 SQL Server）时，组员的
      连接才会被一并强杀，那时才需要约定"谁跑复现先在群里说一声"。
      各人用本机 .\SQLEXPRESS 时不存在这个问题——别人连不到你的实例。
   3. 库名 MilkTeaShop 与排序规则 Chinese_PRC_CI_AS 是【全组约定】，01-schema.sql
      / role.sql / 各自的 sqlcmd 命令都依赖它。改名要全组同步，不要单独改。

   本脚本可重复执行（幂等）：库存在则删后建，不存在则直接建，最终状态都是"一个
   全新的空库"。故复现前无需手工清理。
   ============================================================================ */

SET NOCOUNT ON;
GO

/* ---------------------------------------------------------------------------
   强制切到 master：DROP DATABASE 不能删除"当前所在的库"。
   若组员用 -d MilkTeaShop 或把默认库设成了它，没有这一句脚本就会失败。
   --------------------------------------------------------------------------- */
USE master;
GO

/* ===========================================================================
   【1】库名与排序规则：只在本段声明一次
   =========================================================================== */
DECLARE @db        sysname = N'MilkTeaShop';        -- ★ 库名
DECLARE @collation sysname = N'Chinese_PRC_CI_AS';   -- ★ 排序规则

/* ===========================================================================
   【2】安全闸：防止误改成系统库名（共享实例上的一次手滑就是灾难）
   =========================================================================== */
IF @db IS NULL OR LTRIM(RTRIM(@db)) = N''
    THROW 51000, N'@db 为空：库名必须显式指定，拒绝使用默认值。', 1;

IF @db IN (N'master', N'tempdb', N'model', N'msdb', N'distribution')
    THROW 51001, N'@db 指向系统库：已中止。请检查本脚本【1】处的库名是否被误改。', 1;

/* ===========================================================================
   【3】先删：把库恢复成空
   =========================================================================== */
IF DB_ID(@db) IS NOT NULL
BEGIN
    DECLARE @sql nvarchar(max);

    -- 3.1 断开一切占用连接（含自己开着的 SSMS 窗口），并回滚未提交事务。
    --     没有这一步，只要有一个人连着，DROP 就会报
    --     "Cannot drop database ... because it is currently in use"。
    SET @sql = N'ALTER DATABASE ' + QUOTENAME(@db) + N' SET SINGLE_USER WITH ROLLBACK IMMEDIATE;';
    EXEC sys.sp_executesql @sql;

    -- 3.2 删除。
    SET @sql = N'DROP DATABASE ' + QUOTENAME(@db) + N';';
    EXEC sys.sp_executesql @sql;

    -- 注意：消息前缀不用 "[...]" 形式——sqlcmd 会把行首方括号当作标记并与内容一起
    -- 吞掉（连 -o 写文件也一样），证据输出里就只剩半句话。用 "名字: " 形式。
    PRINT N'00-create: 已删除同名旧库 ' + @db + N'（其中所有表与数据一并消失）';
END
ELSE
    PRINT N'00-create: 实例上无同名库，本次为全新创建。';

/* ===========================================================================
   【4】再建：空库 + 显式排序规则
   ---------------------------------------------------------------------------
   · 排序规则显式写死（而非继承实例默认）：换台机器/换个实例复现时，若某台实例
     的默认排序规则不同，NVARCHAR 的中文排序与比较结果就会不同，"得到相同的库"
     这条复现要求也就不成立。
   · 不指定 .mdf / .ldf 路径：让各人机器上的实例用它自己的默认目录。写死
     C:\... 会让脚本在别的组员机器上直接失败。
   =========================================================================== */
DECLARE @sql_create nvarchar(max) =
    N'CREATE DATABASE ' + QUOTENAME(@db) + N' COLLATE ' + @collation + N';';
EXEC sys.sp_executesql @sql_create;

PRINT N'00-create: 已创建空库 ' + @db + N'（排序规则 ' + @collation + N'）';

/* ===========================================================================
   【5】收尾设置
   ---------------------------------------------------------------------------
   恢复模式设为 SIMPLE：本阶段反复"删库→重建→装载"，完整恢复模式会持续堆积
   事务日志直到磁盘被占满。课堂项目不需要时间点恢复能力。
   =========================================================================== */
DECLARE @sql_recovery nvarchar(max) =
    N'ALTER DATABASE ' + QUOTENAME(@db) + N' SET RECOVERY SIMPLE;';
EXEC sys.sp_executesql @sql_recovery;
GO

/* ===========================================================================
   【6】切到新库
   ---------------------------------------------------------------------------
   USE 不接受变量，故此处必须把库名再写一次——这是本脚本唯一的重复。改动【1】
   的库名时，这两处必须同时改；下面的自检就是用来抓"改漏一处"的。
   =========================================================================== */
USE [MilkTeaShop];
GO

IF DB_NAME() <> N'MilkTeaShop'
    THROW 51002, N'切库失败：当前上下文不是 MilkTeaShop。请检查【1】与【6】两处库名是否一致。', 1;

/* ===========================================================================
   【7】结果确认：输出可直接贴进 evidence/ 作为运行证据
   =========================================================================== */
SELECT
      CAST(N'database'      AS nvarchar(20))  AS item
    , CAST(name              AS nvarchar(128)) AS value
    , CAST(collation_name    AS nvarchar(128)) COLLATE Chinese_PRC_CI_AS AS detail
    , CAST(state_desc        AS nvarchar(128)) COLLATE Chinese_PRC_CI_AS AS state
FROM sys.databases
WHERE name = N'MilkTeaShop'

UNION ALL
SELECT N'server'
     , CAST(SERVERPROPERTY('ServerName') AS nvarchar(128))
     , CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128))
     , CAST(N'-' AS nvarchar(128))

UNION ALL
SELECT N'executed_by'
     , CAST(SUSER_SNAME() AS nvarchar(128))
     , CAST(CONVERT(nvarchar(30), SYSDATETIME(), 120) AS nvarchar(128))
     , CAST(N'-' AS nvarchar(128));

PRINT N'00-create: 完成。空库已就绪，下一步执行 sql/01-schema.sql（建表，要求空库）。';
GO
