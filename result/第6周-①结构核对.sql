/* ============================================================================
   第6周-①结构核对.sql —— 迁移前结构盘点（只读，不改数据）
   ----------------------------------------------------------------------------
   目的：从运行中的 MilkTeaShop（v0.1）反向取事实，与 docs/02 §2.2、§3.1 逐项核对。
   输出即 result/第6周-①结构核对.txt，作为"基线已确认"的证据。
   ============================================================================ */
SET NOCOUNT ON;
USE [MilkTeaShop];
GO

PRINT N'================================================================================';
PRINT N'0. 数据库元数据';
PRINT N'================================================================================';
SELECT
      DB_NAME()                                   AS database_name
    , CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) AS product_version
    , CAST(SERVERPROPERTY('ProductLevel')   AS nvarchar(50)) AS product_level
    , CAST(DATABASEPROPERTYEX('MilkTeaShop', 'Collation') AS nvarchar(100)) AS collation
    , compatibility_level
    , recovery_model_desc
    , create_date
FROM sys.databases
WHERE name = DB_NAME();
GO

PRINT N'================================================================================';
PRINT N'1. 结构指纹（应 = 219 项：110 列 + 17 主码 + 3 UQ + 3 筛选唯一索引 + 25 外码 + 45 CHECK + 16 DEFAULT）';
PRINT N'================================================================================';
SELECT
      (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) AS total_columns
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') AS total_pk
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ') AS total_uq_constraint
    , (SELECT COUNT(*) FROM sys.indexes i
        WHERE i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0
          AND i.is_hypothetical = 0
          AND i.object_id IN (SELECT object_id FROM sys.tables)) AS total_uq_filtered_index
    , (SELECT COUNT(*) FROM sys.foreign_keys) AS total_fk
    , (SELECT COUNT(*) FROM sys.check_constraints) AS total_check
    , (SELECT COUNT(*) FROM sys.default_constraints) AS total_default
    , (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id)
      + (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK')
      + (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ')
      + (SELECT COUNT(*) FROM sys.indexes i
          WHERE i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0
            AND i.is_hypothetical = 0
            AND i.object_id IN (SELECT object_id FROM sys.tables))
      + (SELECT COUNT(*) FROM sys.foreign_keys)
      + (SELECT COUNT(*) FROM sys.check_constraints)
      + (SELECT COUNT(*) FROM sys.default_constraints) AS fingerprint_total;
GO

PRINT N'================================================================================';
PRINT N'2. 每表列数 / 主码 / 候选码 / 外码 / CHECK / 行数';
PRINT N'================================================================================';
SELECT
      t.name AS table_name
    , (SELECT COUNT(*) FROM sys.columns c WHERE c.object_id = t.object_id) AS col_count
    , (SELECT COUNT(*) FROM sys.key_constraints k WHERE k.parent_object_id = t.object_id AND k.type = 'PK') AS pk_count
    , (SELECT COUNT(*) FROM sys.key_constraints k WHERE k.parent_object_id = t.object_id AND k.type = 'UQ') AS uq_count
    , (SELECT COUNT(*) FROM sys.indexes i
        WHERE i.object_id = t.object_id AND i.is_unique = 1
          AND i.is_primary_key = 0 AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0) AS uq_filtered_count
    , (SELECT COUNT(*) FROM sys.foreign_keys f WHERE f.parent_object_id = t.object_id) AS fk_count
    , (SELECT COUNT(*) FROM sys.check_constraints cc WHERE cc.parent_object_id = t.object_id) AS ck_count
    , (SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = t.object_id AND p.index_id IN (0,1)) AS row_count
FROM sys.tables t
ORDER BY t.name;
GO

PRINT N'================================================================================';
PRINT N'3. 110 列清单（列序 / 列名 / 类型 / 长度或精度 / 可空 / 默认值）';
PRINT N'================================================================================';
SELECT
      t.name AS table_name
    , c.column_id
    , c.name AS column_name
    , ty.name AS data_type
    , c.max_length
    , c.precision
    , c.scale
    , c.is_nullable
    , dc.name AS default_name
    , dc.definition AS default_definition
FROM sys.tables t
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.default_constraints dc
       ON dc.parent_object_id = t.object_id AND dc.parent_column_id = c.column_id
ORDER BY t.name, c.column_id;
GO

PRINT N'================================================================================';
PRINT N'4. 键清单（主码 / 候选码 UNIQUE / 筛选唯一索引）';
PRINT N'================================================================================';
SELECT
      t.name AS table_name
    , kc.name AS key_name
    , kc.type_desc AS key_type
    , STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS key_columns
FROM sys.key_constraints kc
JOIN sys.tables t ON t.object_id = kc.parent_object_id
JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
GROUP BY t.name, kc.name, kc.type_desc
UNION ALL
SELECT
      t.name AS table_name
    , i.name AS key_name
    , 'FILTERED UNIQUE INDEX' AS key_type
    , STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS key_columns
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0
GROUP BY t.name, i.name
ORDER BY table_name, key_type, key_name;
GO

PRINT N'================================================================================';
PRINT N'5. 外码清单（25 条，含级联删除动作）';
PRINT N'================================================================================';
SELECT
      fk.name AS fk_name
    , tp.name AS parent_table
    , STRING_AGG(cp.name, ',') WITHIN GROUP (ORDER BY fkc.constraint_column_id) AS parent_columns
    , tr.name AS referenced_table
    , STRING_AGG(cr.name, ',') WITHIN GROUP (ORDER BY fkc.constraint_column_id) AS referenced_columns
    , fk.delete_referential_action_desc AS on_delete
FROM sys.foreign_keys fk
JOIN sys.tables tp ON tp.object_id = fk.parent_object_id
JOIN sys.tables tr ON tr.object_id = fk.referenced_object_id
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id
JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id AND cr.column_id = fkc.referenced_column_id
GROUP BY fk.name, tp.name, tr.name, fk.delete_referential_action_desc
ORDER BY tp.name, fk.name;
GO

PRINT N'================================================================================';
PRINT N'6. CHECK 约束清单（45 条）';
PRINT N'================================================================================';
SELECT
      t.name AS table_name
    , cc.name AS check_name
    , cc.definition
FROM sys.check_constraints cc
JOIN sys.tables t ON t.object_id = cc.parent_object_id
ORDER BY t.name, cc.name;
GO

PRINT N'================================================================================';
PRINT N'7. 默认值清单（16 条）';
PRINT N'================================================================================';
SELECT
      t.name AS table_name
    , c.name AS column_name
    , dc.name AS default_name
    , dc.definition
FROM sys.default_constraints dc
JOIN sys.tables t ON t.object_id = dc.parent_object_id
JOIN sys.columns c ON c.object_id = dc.parent_object_id AND c.column_id = dc.parent_column_id
ORDER BY t.name, c.column_id;
GO

PRINT N'================================================================================';
PRINT N'8. 与 docs/02 的对照结论（人工核对，结构化自检）';
PRINT N'================================================================================';
SELECT
      N'17 张表 / 110 列 / 17 主码 / 3 UQ + 3 筛选唯一索引 / 25 外码 / 45 CHECK / 16 DEFAULT' AS expected
    , CASE WHEN
        (SELECT COUNT(*) FROM sys.tables) = 17
        AND (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON c.object_id = t.object_id) = 110
        AND (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') = 17
        AND (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ') = 3
        AND (SELECT COUNT(*) FROM sys.indexes i
              WHERE i.is_unique = 1 AND i.is_primary_key = 0 AND i.is_unique_constraint = 0
                AND i.is_hypothetical = 0
                AND i.object_id IN (SELECT object_id FROM sys.tables)) = 3
        AND (SELECT COUNT(*) FROM sys.foreign_keys) = 25
        AND (SELECT COUNT(*) FROM sys.check_constraints) = 45
        AND (SELECT COUNT(*) FROM sys.default_constraints) = 16
      THEN N'MATCH：与 docs/02 §2.2 / §3.1 / §5.8 及第三周复现指纹一致'
      ELSE N'DIFF：存在与文档不一致的结构项，需逐项核对'
      END AS verification_result;
GO
