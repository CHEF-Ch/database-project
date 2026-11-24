param (
    [Parameter(Mandatory)]
    [ValidatePattern('^MilkTeaShop_week11_[A-Za-z0-9_]{1,80}$')]
    [string]$Database,
    [string]$Server = '.\SQLEXPRESS',
    # 只建到 v2.2，用于生成第十一周 ① 的迁移前基线证据
    [switch]$StopBeforeAudit
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ('milktea-week11-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null
Push-Location -LiteralPath $root

try {
    & sqlcmd -S $Server -E -C -b -d master -Q "IF DB_ID(N'$Database') IS NOT NULL THROW 51191,'Acceptance database already exists',1; CREATE DATABASE [$Database] COLLATE Chinese_PRC_CI_AS;"
    if ($LASTEXITCODE -ne 0) { throw "Could not create $Database." }

    # v1.0 基线：与 week10 验收库同一套种子链
    foreach ($name in @('01-schema', 'constraint', '02-seed', 'view', 'role', 'migration', 'app_login', 'account_read')) {
        $source = Get-Content -Raw -Encoding UTF8 -LiteralPath "sql/$name.sql"
        $source = $source.Replace('MilkTeaShop_v01', $Database).Replace('USE [MilkTeaShop];', "USE [$Database];")
        $script = Join-Path $temp "$name.sql"
        Set-Content -Encoding UTF8 -LiteralPath $script -Value $source
        & sqlcmd -S $Server -E -C -b -f 65001 -d $Database -i $script
        if ($LASTEXITCODE -ne 0) { throw "$name failed on $Database." }
    }

    # v2.0 → v2.1 → v2.2
    foreach ($script in @('sql/migration_v2_0.sql', 'scripts/v2_migration_test.sql',
                          'sql/migration_v2_1_usage.sql', 'sql/02-seed-v2-usage.sql',
                          'sql/migration_v2_2_app_user.sql')) {
        & sqlcmd -S $Server -E -C -b -f 65001 -d $Database -i $script
        if ($LASTEXITCODE -ne 0) { throw "$script failed on $Database." }
    }

    if (-not $StopBeforeAudit) {
        & sqlcmd -S $Server -E -C -b -f 65001 -d $Database -i sql/migration_v2_3_audit.sql
        if ($LASTEXITCODE -ne 0) { throw "migration_v2_3_audit.sql failed on $Database." }
    }

    & sqlcmd -S $Server -E -C -b -W -d $Database -Q "SET NOCOUNT ON; SELECT DB_NAME() AS db, CONVERT(nvarchar(20), value) AS schema_version FROM sys.extended_properties WHERE class=0 AND name=N'milktea_schema_version';"
    if ($LASTEXITCODE -ne 0) { throw "Could not read schema version of $Database." }
}
finally {
    Pop-Location
    Get-ChildItem -LiteralPath $temp -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
    Remove-Item -LiteralPath $temp
}
