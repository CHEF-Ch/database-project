<#
.SYNOPSIS
    一次性开启 SQL Server 混合认证（SQL Server 认证 + Windows 认证）。

.DESCRIPTION
    SQL Server 默认是"仅 Windows 认证"（注册表 LoginMode = 1）。此模式下
    sql/app_login.sql 创建的 SQL 登录虽然存在，但无法用于连接认证，应用会报
    "Login failed for user 'login_xxx'"。

    本脚本把 LoginMode 改为 2 并重启 SQL Server 服务，使受限 SQL 登录可用。
    只需执行一次；已是混合模式时直接退出。

    注意：需要以【管理员】权限运行（改注册表 + 重启服务）。
    重启会断开该实例上的所有连接，请在无人作业时执行。

.PARAMETER Instance
    实例注册表键名，默认 MSSQL17.SQLEXPRESS。

.PARAMETER Service
    Windows 服务名，默认 MSSQL$SQLEXPRESS。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\enable-mixed-auth.ps1
#>
#Requires -RunAsAdministrator
param(
    [string]$Instance = 'MSSQL17.SQLEXPRESS',
    [string]$Service  = 'MSSQL$SQLEXPRESS',
    [string]$Server   = '.\SQLEXPRESS'
)

$ErrorActionPreference = 'Stop'

$key = "HKLM:\SOFTWARE\Microsoft\Microsoft SQL Server\$Instance\MSSQLServer"
if (-not (Test-Path $key)) {
    throw "找不到实例注册表键：$key。请确认 -Instance 参数。"
}

$current = (Get-ItemProperty -Path $key -Name LoginMode).LoginMode
Write-Host "当前 LoginMode = $current  （1 = 仅 Windows，2 = 混合模式）"

if ($current -eq 2) {
    Write-Host '已是混合模式，无需改动。'
    exit 0
}

Set-ItemProperty -Path $key -Name LoginMode -Value 2
Write-Host '已写入 LoginMode = 2，正在重启服务...'

Restart-Service -Name $Service -Force
Start-Sleep -Seconds 3

$now = (Get-ItemProperty -Path $key -Name LoginMode).LoginMode
if ($now -ne 2) { throw "设置未生效：LoginMode = $now" }

Write-Host "重启后 LoginMode = $now"
Write-Host '校验 IsIntegratedSecurityOnly（应为 0）：'

# 不只打印：真断言一次，避免"看着像成功"
$raw = & sqlcmd -S $Server -E -C -h -1 -W -Q "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('IsIntegratedSecurityOnly') AS int);"
$value = ($raw | Where-Object { $_ -match '^\s*\d+\s*$' } | Select-Object -First 1)
if ($null -eq $value) { throw '无法读取 IsIntegratedSecurityOnly，请检查实例名与服务状态' }
$value = $value.Trim()
Write-Host "IsIntegratedSecurityOnly = $value"
if ($value -ne '0') { throw "混合模式未生效（IsIntegratedSecurityOnly = $value，应为 0）" }

Write-Host '完成：混合模式已生效。'
Write-Host "后续可执行：sqlcmd -S '$Server' -E -C -b -f 65001 -i sql/app_login.sql"
