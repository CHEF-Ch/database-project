/* ============================================================================
   create_v01_copy.sql —— 由 v0.1 源库生成 v1.0 迁移副本（可重复执行）
   ----------------------------------------------------------------------------
   做的事：把 MilkTeaShop(v0.1) 备份 → 还原为 MilkTeaShop_v01，然后由调用方
           再执行 sql/migration.sql。副本已存在时先删除，保证"从头再跑"成立。

   为什么需要它：migration.sql 的预检要求目标库是 v0.1（110 列 / 45 CHECK），
   直接对已迁移的 v1.0 重跑会 THROW；所以要有一个明确的重建入口。

   路径不写死：使用实例自带的 InstanceDefaultBackupPath / InstanceDefaultDataPath，
   换机器不需要改脚本（这也是 .bak 不入库但仍能复现的前提）。

   用法：
     sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i scripts/create_v01_copy.sql
     sqlcmd -S '.\SQLEXPRESS' -E -C -b -f 65001 -i sql/migration.sql
   ============================================================================ */

SET NOCOUNT ON;
USE master;
GO

IF DB_ID(N'MilkTeaShop') IS NULL
BEGIN
    THROW 51010, N'源库 MilkTeaShop 不存在：请先按 sql/README.md 的 00→01→constraint→seed→view→role 顺序复现 v0.1。', 1;
END

/* 副本已存在则先删除（否则 RESTORE 会报"数据库已存在"） */
IF DB_ID(N'MilkTeaShop_v01') IS NOT NULL
BEGIN
    ALTER DATABASE [MilkTeaShop_v01] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
    DROP DATABASE [MilkTeaShop_v01];
    PRINT N'已删除旧副本 MilkTeaShop_v01';
END
GO

DECLARE @bakdir  nvarchar(4000) = CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS nvarchar(4000));
DECLARE @datadir nvarchar(4000) = CAST(SERVERPROPERTY('InstanceDefaultDataPath')   AS nvarchar(4000));
IF RIGHT(@bakdir, 1)  <> N'\' SET @bakdir  = @bakdir  + N'\';
IF RIGHT(@datadir, 1) <> N'\' SET @datadir = @datadir + N'\';

DECLARE @bak nvarchar(4000) = @bakdir  + N'MilkTeaShop_v01_seed.bak';
DECLARE @mdf nvarchar(4000) = @datadir + N'MilkTeaShop_v01.mdf';
DECLARE @ldf nvarchar(4000) = @datadir + N'MilkTeaShop_v01_log.ldf';
DECLARE @sql nvarchar(max);

SET @sql = N'BACKUP DATABASE [MilkTeaShop] TO DISK = N''' + @bak + N''' WITH INIT, COPY_ONLY, CHECKSUM;';
EXEC (@sql);
PRINT N'已备份源库到：' + @bak;

SET @sql = N'RESTORE DATABASE [MilkTeaShop_v01] FROM DISK = N''' + @bak + N''' WITH '
         + N'MOVE N''MilkTeaShop'' TO N''' + @mdf + N''', '
         + N'MOVE N''MilkTeaShop_log'' TO N''' + @ldf + N''', RECOVERY;';
EXEC (@sql);
PRINT N'已还原副本 MilkTeaShop_v01（此时仍是 v0.1，下一步执行 sql/migration.sql）';
GO

/* 副本当前应为 v0.1：110 列 / 45 CHECK（若已是 111/60 说明迁移过，需重建） */
USE [MilkTeaShop_v01];
GO
SELECT
      (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) AS columns
    , (SELECT COUNT(*) FROM sys.check_constraints)                                        AS checks;
GO
