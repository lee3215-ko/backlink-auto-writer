# PowerShell에서 실행:  .\run.ps1
Set-Location $PSScriptRoot
Write-Host "백링크 프로그램 실행 중..." -ForegroundColor Cyan
python main.py
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "실패. 설치:" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt"
    Write-Host "  playwright install chromium"
    Read-Host "Enter 키로 종료"
}
