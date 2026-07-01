# Playwright Chromium을 ASCII 임시 경로에 설치 후 dist로 복사 (한글 경로 잠금 회피)
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$PwTemp = Join-Path $env:LOCALAPPDATA "BacklinkWriter_build\ms-playwright"
$PwDist = Join-Path $Root "dist\BacklinkWriter\ms-playwright"

if (Test-Path $PwTemp) { Remove-Item $PwTemp -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $PwTemp | Out-Null

$env:PLAYWRIGHT_BROWSERS_PATH = $PwTemp
& python -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw "playwright install failed" }

if (Test-Path $PwDist) { Remove-Item $PwDist -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path (Split-Path $PwDist -Parent) | Out-Null
Copy-Item -Path $PwTemp -Destination $PwDist -Recurse -Force
Write-Host "[Playwright] dist 복사 완료: $PwDist"
