# Builds dist\myoverlay\ (and myoverlay-win64.zip) - the shareable launcher.
#
# Run from the repo root:  powershell -File packaging\build_exe.ps1
# Requires: uv (deps come from the project venv), internet on first run
# (downloads MinGit and ffmpeg into packaging\vendor\, cached afterwards).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pack = Join-Path $root "packaging"
$vendor = Join-Path $pack "vendor"
New-Item -ItemType Directory -Force $vendor | Out-Null

function Get-Zip($url, $dest) {
    Write-Host "downloading $url"
    $tmp = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName() + ".zip")
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Expand-Archive -Path $tmp -DestinationPath $dest -Force
    Remove-Item $tmp
}

# --- MinGit (portable git, ~45 MB) ---
$gitDir = Join-Path $vendor "git"
if (-not (Test-Path (Join-Path $gitDir "cmd\git.exe"))) {
    $rel = Invoke-RestMethod "https://api.github.com/repos/git-for-windows/git/releases/latest" -UseBasicParsing
    $asset = $rel.assets | Where-Object { $_.name -match "^MinGit-.*-64-bit\.zip$" } | Select-Object -First 1
    if (-not $asset) { throw "MinGit asset not found in latest git-for-windows release" }
    Get-Zip $asset.browser_download_url $gitDir
}

# --- ffmpeg (gyan.dev release essentials, ~90 MB) ---
$ffDir = Join-Path $vendor "ffmpeg"
if (-not (Test-Path (Join-Path $ffDir "ffmpeg.exe"))) {
    $tmpDir = Join-Path $env:TEMP ("ff_" + [IO.Path]::GetRandomFileName())
    Get-Zip "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" $tmpDir
    New-Item -ItemType Directory -Force $ffDir | Out-Null
    $bin = Get-ChildItem -Recurse $tmpDir -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $bin.FullName $ffDir
    Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") $ffDir
    Remove-Item -Recurse -Force $tmpDir
}

# --- build ---
Set-Location $root
uv sync
uv pip install pyinstaller
Set-Location $pack
uv run pyinstaller --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $pack "build") myoverlay.spec

# --- zip for sharing ---
$distDir = Join-Path $root "dist\myoverlay"
$zip = Join-Path $root "dist\myoverlay-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $distDir -DestinationPath $zip
Write-Host "done: $zip"
