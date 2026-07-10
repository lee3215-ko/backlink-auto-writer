param(
    [string]$Notes = "업데이트",
    [ValidateSet("patch", "minor", "major", "none")]
    [string]$Bump = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root
. (Join-Path $PSScriptRoot "gh-env.ps1")

$GitSafe = (Resolve-Path $Root).Path
function Invoke-RepoGit {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    # git warning(stderr)이 $ErrorActionPreference=Stop 에서 예외로 끊기지 않게 함
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & git.exe -c "safe.directory=$GitSafe" @Args
        return $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Write-TextNoBom([string]$Path, [string]$Text) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Text, $utf8NoBom)
}

function Read-DeployConfig {
    Get-Content (Join-Path $Root "deploy.json") -Raw | ConvertFrom-Json
}

function Read-AppVersion($cfg) {
    $path = Join-Path $Root $cfg.version.file
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $text = [System.IO.File]::ReadAllText($path, $utf8)
    $var = [regex]::Escape($cfg.version.variable)
    if ($text -match "${var}\s*=\s*`"([^`"]+)`"") {
        return $Matches[1]
    }
    throw "버전을 찾을 수 없습니다: $($cfg.version.variable)"
}

function Set-AppVersion($cfg, [string]$Version) {
    $path = Join-Path $Root $cfg.version.file
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $text = [System.IO.File]::ReadAllText($path, $utf8)
    $var = [regex]::Escape($cfg.version.variable)
    $text = $text -replace "${var}\s*=\s*`"[^`"]+`"", "$($cfg.version.variable) = `"$Version`""
    Write-TextNoBom $path $text
}

function Bump-Version([string]$Version, [string]$Part) {
    $parts = $Version.Split(".")
    if ($parts.Count -lt 3) { throw "버전 형식 오류: $Version" }
    [int]$major = $parts[0]
    [int]$minor = $parts[1]
    [int]$patch = $parts[2]
    switch ($Part) {
        "major" { $major++; $minor = 0; $patch = 0 }
        "minor" { $minor++; $patch = 0 }
        "patch" { $patch++ }
        "none" { }
    }
    return "$major.$minor.$patch"
}

function Write-VersionJson($cfg, [string]$Version, [string]$ReleaseNotes) {
    $tag = "v$Version"
    $owner = $cfg.github_owner
    $repo = $cfg.github_repo
    $asset = $cfg.release_asset
    $downloadUrlLatest = "https://github.com/$owner/$repo/releases/latest/download/$asset"
    $downloadUrlVersioned = "https://github.com/$owner/$repo/releases/download/$tag/$asset"
    $assetId = $null
    try {
        $assetId = gh api "repos/$owner/$repo/releases/tags/$tag" --jq ".assets[0].id" 2>$null
    } catch {
        $assetId = $null
    }
    $apiDownloadUrl = $null
    if ($assetId) {
        $apiDownloadUrl = "https://api.github.com/repos/$owner/$repo/releases/assets/$assetId"
    }
    $payload = [ordered]@{
        version = $Version
        url     = if ($apiDownloadUrl) { $apiDownloadUrl } else { $downloadUrlVersioned }
        download_url = $downloadUrlVersioned
        notes   = $ReleaseNotes
    }
    if ($apiDownloadUrl) {
        $payload.api_download_url = $apiDownloadUrl
        $payload.asset_id = [int]$assetId
    }
    $payload.download_urls = @(
        $(if ($apiDownloadUrl) { $apiDownloadUrl }),
        $downloadUrlVersioned,
        $downloadUrlLatest
    ) | Where-Object { $_ }
    $json = $payload | ConvertTo-Json -Depth 4
    Write-TextNoBom (Join-Path $Root "version.json") ($json + "`n")
}

function Ensure-GitRemote($cfg) {
    if (-not (Test-Path (Join-Path $Root ".git"))) {
        Invoke-RepoGit init | Out-Null
    }
    $branch = Invoke-RepoGit branch --show-current 2>$null
    if ($branch -and $branch -ne "main") {
        Invoke-RepoGit branch -M main | Out-Null
    } elseif (-not $branch) {
        Invoke-RepoGit checkout -B main 2>$null | Out-Null
    }
    $remoteUrl = "https://github.com/$($cfg.github_owner)/$($cfg.github_repo).git"
    $hasOrigin = @(Invoke-RepoGit remote 2>$null) -contains "origin"
    if (-not $hasOrigin) {
        Invoke-RepoGit remote add origin $remoteUrl
        Write-Host "[git] origin: $remoteUrl"
    }
}

function Ensure-GhAuth {
    Invoke-Gh auth status *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "gh auth login 이 필요합니다."
    }
}

$cfg = Read-DeployConfig
$bumpPart = if ($Bump) { $Bump } else { $cfg.default_bump }
$current = Read-AppVersion $cfg
$newVersion = Bump-Version $current $bumpPart
$tag = "v$newVersion"
$displayName = if ($cfg.app_display_name) { $cfg.app_display_name } else { $cfg.github_repo }

Write-Host "============================================"
Write-Host " $displayName 배포"
Write-Host " 버전: $current -> $newVersion"
Write-Host "============================================"

Set-AppVersion $cfg $newVersion
Write-VersionJson $cfg $newVersion $Notes

if (-not $SkipBuild) {
    Write-Host "[1/5] 빌드..."
    $buildScript = Join-Path $Root $cfg.build.script
    & $buildScript
    if ($LASTEXITCODE -ne 0) { throw "빌드 실패" }
}

$distDir = Join-Path $Root ($cfg.build.dist_dir -replace "/", "\")
if (-not (Test-Path $distDir)) {
    throw "빌드 결과 없음: $($cfg.build.dist_dir)"
}

Write-Host "[2/5] zip 생성..."
$zipPath = Join-Path $Root "dist\$($cfg.release_asset)"
if (-not (Test-Path (Split-Path $zipPath -Parent))) {
    New-Item -ItemType Directory -Path (Split-Path $zipPath -Parent) | Out-Null
}
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path $distDir -DestinationPath $zipPath -Force

Ensure-GhInstalled
Ensure-GitRemote $cfg
Ensure-GhAuth

Write-Host "[3/5] GitHub push..."
$addArgs = @()
foreach ($item in $cfg.git_add) { $addArgs += $item }
if ($addArgs.Count -gt 0) { Invoke-RepoGit add @addArgs }
Invoke-RepoGit add deploy.json deploy.bat version.json scripts assets 2>$null
Invoke-RepoGit add -u

if (Invoke-RepoGit status --porcelain) {
    Invoke-RepoGit commit -m "Release $newVersion"
}

Invoke-RepoGit push -u origin main
if ($LASTEXITCODE -ne 0) {
    Invoke-RepoGit pull origin main --rebase
    Invoke-RepoGit push -u origin main
}

Write-Host "[4/5] GitHub Release..."
if (Test-GhRelease $tag) {
    Invoke-Gh release upload $tag $zipPath --clobber
    Invoke-Gh release edit $tag --notes $Notes --title $newVersion
} else {
    Invoke-Gh release create $tag $zipPath --title $newVersion --notes $Notes --latest
}

Write-Host "[5/5] version.json asset_id 갱신..."
Write-VersionJson $cfg $newVersion $Notes
if (Invoke-RepoGit status --porcelain version.json) {
    Invoke-RepoGit add version.json
    Invoke-RepoGit commit -m "Update version.json download URLs for $newVersion"
    Invoke-RepoGit push origin main
}

Write-Host ""
Write-Host "배포 완료: $newVersion"
Write-Host "https://github.com/$($cfg.github_owner)/$($cfg.github_repo)/releases/tag/$tag"
