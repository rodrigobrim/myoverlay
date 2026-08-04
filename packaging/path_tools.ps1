# Stages the commands that must be callable by NAME after an install - `uv`
# and `mt` - into the payload root (dist\myoverlay).
#
# Why the root, and not the spec's datas: PyInstaller 6 puts every bundled
# data file under _internal\, and only the payload ROOT becomes the install
# directory - which build_msi.ps1 already puts on the machine PATH (the
# AppPath component in Product.wxs). A file dropped here is therefore on PATH
# after install, with no extra PATH entry to add or to clean up, and pointing
# at the installed copy rather than at anything else on the machine.
#
# Both build_exe.ps1 (zip) and build_msi.ps1 (installer) call this, so the two
# distributions cannot disagree about what is on PATH. It is idempotent and
# costs nothing once packaging\vendor\uv is populated.

param([Parameter(Mandatory = $true)][string]$Payload)

$ErrorActionPreference = "Stop"
$pack = $PSScriptRoot
$vendor = Join-Path $pack "vendor"

if (-not (Test-Path (Join-Path $Payload "myoverlay.exe"))) {
    throw "Payload missing: $Payload - run packaging\build_exe.ps1 first."
}

# --- uv (astral-sh, ~35 MB extracted) ---
# Cached in packaging\vendor\uv like MinGit and ffmpeg. The archive holds
# uv.exe and uvx.exe at its root; only uv.exe is shipped.
$uvDir = Join-Path $vendor "uv"
$uvVendored = Join-Path $uvDir "uv.exe"
if (-not (Test-Path $uvVendored)) {
    New-Item -ItemType Directory -Force $vendor | Out-Null
    Write-Host "Downloading uv (windows x86_64)..."
    # Download to a staging name and rename only on success, so a connection
    # dropped mid-transfer cannot leave a truncated archive that the Test-Path
    # above would read as "cached".
    $tmp = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName() + ".zip")
    Invoke-WebRequest -UseBasicParsing -OutFile $tmp `
        -Uri "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    Expand-Archive -Path $tmp -DestinationPath $uvDir -Force
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $uvVendored)) {
        throw "uv.exe not found in the downloaded archive - layout changed?"
    }
}

Copy-Item $uvVendored (Join-Path $Payload "uv.exe") -Force
Copy-Item (Join-Path $pack "mt.cmd") (Join-Path $Payload "mt.cmd") -Force

$uvVersion = (& $uvVendored --version 2>$null | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($uvVersion)) { $uvVersion = "unknown" }
Write-Host "PATH commands staged -> uv ($uvVersion) | mt (shim to myoverlay.exe)"
