function Refresh-ShellPath {
    $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Get-GhExe {
    Refresh-ShellPath
    $cmd = Get-Command gh -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        "$env:ProgramFiles\GitHub CLI\gh.exe",
        "${env:ProgramFiles(x86)}\GitHub CLI\gh.exe",
        "$env:LOCALAPPDATA\Programs\GitHub CLI\gh.exe"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

function Invoke-Gh {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GhArgs)
    $gh = Get-GhExe
    if (-not $gh) { throw "GitHub CLI(gh) 없음. winget install GitHub.cli" }
    & $gh @GhArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Test-GhRelease([string]$Tag) {
    $gh = Get-GhExe
    if (-not $gh) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & $gh release view $Tag 2>$null | Out-Null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $prev
    return $ok
}

function Ensure-GhInstalled {
    if (Get-GhExe) { return }
    Write-Host "GitHub CLI 설치 중..."
    winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements | Out-Null
    Refresh-ShellPath
    if (-not (Get-GhExe)) { throw "gh를 찾을 수 없습니다. PowerShell을 다시 열어 주세요." }
}
