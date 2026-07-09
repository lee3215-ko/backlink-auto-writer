# PowerShell 실행:  .\run.ps1
# (프로젝트 폴더에서)  Set-Location -LiteralPath "E:\백링크 프로그램"; .\run.ps1

Set-Location -LiteralPath $PSScriptRoot

$main = Join-Path $PSScriptRoot "main.py"
if (-not (Test-Path -LiteralPath $main)) {
    Write-Host "main.py 를 찾을 수 없습니다: $main" -ForegroundColor Red
    Read-Host "Enter 키로 종료"
    exit 1
}

Write-Host "백링크 프로그램 실행 중..." -ForegroundColor Cyan
Write-Host "  경로: $PSScriptRoot" -ForegroundColor DarkGray
Write-Host "  (창을 닫으면 PowerShell 프롬프트로 돌아옵니다 — 정상 동작)" -ForegroundColor DarkGray
Write-Host ""

python $main
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ""
    Write-Host "실행 실패 (종료 코드 $code)" -ForegroundColor Red
    Write-Host "  올바른 명령: python main.py  (main.pycd 아님)" -ForegroundColor Yellow
    Write-Host "  설치: pip install -r requirements.txt" -ForegroundColor Yellow
    Write-Host "        playwright install chromium" -ForegroundColor Yellow
    Read-Host "Enter 키로 종료"
}

exit $code
