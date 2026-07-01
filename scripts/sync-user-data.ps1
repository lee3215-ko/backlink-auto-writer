# 개발(터미널) logs/ 데이터를 exe용 %APPDATA%\BacklinkWriter 로 동기화
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$DevLogs = Join-Path $Root "logs"
$AppData = Join-Path $env:APPDATA "BacklinkWriter"

$DataFiles = @(
    "app_state.json",
    "post_history.json",
    "board_catalog.json",
    "board_probed.json",
    "auto_search_state.json",
    "backlink.log"
)

if (-not (Test-Path $DevLogs)) {
    Write-Host "[데이터] logs 폴더 없음 — 건너뜀"
    exit 0
}

New-Item -ItemType Directory -Force -Path $AppData | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backup = Join-Path $AppData "backup_$stamp"
$synced = 0

foreach ($name in $DataFiles) {
    $src = Join-Path $DevLogs $name
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $AppData $name
    if (Test-Path $dst) {
        New-Item -ItemType Directory -Force -Path $backup | Out-Null
        Copy-Item $dst (Join-Path $backup $name) -Force
    }
    Copy-Item $src $dst -Force
    $synced++
    Write-Host "  -> $name"
}

Write-Host "[데이터] AppData 동기화 완료 ($synced 개) — $AppData"
if ($synced -gt 0 -and (Test-Path $backup)) {
    Write-Host "[데이터] 기존 AppData 백업: $backup"
}
