/* ============================================================================
   create_test_db.sql —— ⑦ 集成测试沙箱：把 v1.0 复制成 MilkTeaShop_test
   ----------------------------------------------------------------------------
   做的事：BACKUP … COPY_ONLY 备份 MilkTeaShop_v01（v1.0，指纹 236），
           再 RESTORE 为 MilkTeaShop_test。源库全程只读，绝不被改动。

   为什么需要它（docs/09-应用开发/测试用例.md §2.1）：
     · I 组「并发/幂等/故障注入」必须**真提交**才测得出竞态，而 v01 上的
       其它用例走「外层事务 + SAVEPOINT 全回滚」，两者不能共用一个库；
     · 真提交会产生长期残留数据，故写入一律落在可随时重建的测试库。

   ⚠ 与 scripts/create_v01_copy.sql 的区别：
     · create_v01_copy.sql 作用于 v0.1 源库、会 DROP **MilkTeaShop_v01**（应用库），
       是「从 v0.1 重做迁移副本」的入口，**不属于测试用途，别拿它建测试库**；
     · 本脚本只删/建 **MilkTeaShop_test**，不碰 MilkTeaShop 与 MilkTeaShop_v01。

   幂等：可重复执行。测试库已存在则先删后建，每次都是 v01 的干净副本。
   RESTORE 会带上库内的角色与授权（role_customer / role_app 等），因此
   DB_AUTH=sql 在测试库里同样可用。

   用法（仓库根目录）：
     sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i scripts/create_test_db.sql
   ============================================================================ */

SET NOCOUNT ON;
USE master;
GO

IF DB_ID(N'MilkTeaShop_v01') IS NULL
BEGIN
    THROW 51020, N'源库 MilkTeaShop_v01 不存在：先按 README 复现 v0.1 并执行 sql/migration.sql。', 1;
END

IF DB_ID(N'MilkTeaShop_test') IS NOT NULL
BEGIN
    ALTER DATABASE [MilkTeaShop_test] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE [MilkTeaShop_test];
    PRINT N'已删除旧测试库 MilkTeaShop_test';
END
GO

DECLARE @bakdir  nvarchar(4000) = CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000));
DECLARE @datadir nvarchar(4000) = CAST(SERVERPROPERTY('InstanceDefaultDataPath')   AS nvarchar(4000));
IF RIGHT(@bakdir, 1)  <> N'\' SET @bakdir  = @bakdir  + N'\';
IF RIGHT(@datadir, 1) <> N'\' SET @datadir = @datadir + N'\';

DECLARE @bak nvarchar(4000) = @bakdir  + N'MilkTeaShop_test_seed.bak';
DECLARE @mdf nvarchar(4000) = @datadir + N'MilkTeaShop_test.mdf';
DECLARE @ldf nvarchar(4000) = @datadir + N'MilkTeaShop_test_log.ldf';
DECLARE @sql nvarchar(max);

/* COPY_ONLY：不打断 v01 现有的备份链（v01 由 create_v01_copy.sql 的完整备份派生） */
SET @sql = N'BACKUP DATABASE [MilkTeaShop_v01] TO DISK = N''' + @bak + N''' WITH INIT, COPY_ONLY, CHECKSUM;';
EXEC (@sql);
PRINT N'已备份源库 MilkTeaShop_v01 → ' + @bak;

/* 逻辑文件名沿用 v01 的 MilkTeaShop / MilkTeaShop_log（它由 v0.1 的备份 MOVE 而来） */
SET @sql = N'RESTORE DATABASE [MilkTeaShop_test] FROM DISK = N''' + @bak + N''' WITH '
         + N'MOVE N''MilkTeaShop''     TO N''' + @mdf + N''', '
         + N'MOVE N''MilkTeaShop_log'' TO N''' + @ldf + N''', REPLACE, RECOVERY, CHECKSUM;';
EXEC (@sql);
PRINT N'已还原测试库 MilkTeaShop_test（源库只读，未改动）';
GO

USE [MilkTeaShop_test];
GO

/* 自检：测试库应当与 v1.0 同结构（111 列 / 25 外码 / 60 CHECK），且 seed 行数不变 */
SELECT
      (SELECT COUNT(*) FROM sys.columns  c JOIN sys.tables t ON c.object_id = t.object_id) AS columns_111
    , (SELECT COUNT(*) FROM sys.foreign_keys)                                              AS fk_25
    , (SELECT COUNT(*) FROM sys.check_constraints)                                         AS ck_60
    , (SELECT COUNT(*) FROM dbo.item_product)  AS item_product_5
    , (SELECT COUNT(*) FROM dbo.shop_order)    AS shop_order_7
    , (SELECT COUNT(*) FROM dbo.inv_stock_log) AS inv_stock_log_36;

PRINT N'================================================================================';
PRINT N'测试库就绪：DB_NAME=MilkTeaShop_test（上方六列应依次为 111 / 25 / 60 / 5 / 7 / 36）';
PRINT N'================================================================================';
GO
