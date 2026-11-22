param (
    [Parameter(Mandatory)]
    [ValidatePattern('^MilkTeaShop_week10_[A-Za-z0-9_]{1,80}$')]
    [string]$Database,
    [string]$Server = '.\SQLEXPRESS'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ('milktea-week10-' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null
Push-Location -LiteralPath $root

try {
    & sqlcmd -S $Server -E -C -b -d master -Q "IF DB_ID(N'$Database') IS NOT NULL THROW 51190,'Acceptance database already exists',1; CREATE DATABASE [$Database] COLLATE Chinese_PRC_CI_AS;"
    if ($LASTEXITCODE -ne 0) { throw "Could not create $Database." }

    foreach ($name in @('01-schema', 'constraint', '02-seed', 'view', 'role', 'migration', 'app_login', 'account_read')) {
        $source = Get-Content -Raw -Encoding UTF8 -LiteralPath "sql/$name.sql"
        $source = $source.Replace('MilkTeaShop_v01', $Database).Replace('USE [MilkTeaShop];', "USE [$Database];")
        $script = Join-Path $temp "$name.sql"
        Set-Content -Encoding UTF8 -LiteralPath $script -Value $source
        & sqlcmd -S $Server -E -C -b -f 65001 -d $Database -i $script
        if ($LASTEXITCODE -ne 0) { throw "$name failed on $Database." }
    }

    foreach ($script in @('sql/migration_v2_0.sql', 'scripts/v2_migration_test.sql',
                          'sql/migration_v2_1_usage.sql', 'sql/02-seed-v2-usage.sql')) {
        & sqlcmd -S $Server -E -C -b -f 65001 -d $Database -i $script
        if ($LASTEXITCODE -ne 0) { throw "$script failed on $Database." }
    }
}
finally {
    Pop-Location
    Get-ChildItem -LiteralPath $temp -File | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
    Remove-Item -LiteralPath $temp
}
