# Builds dist\MyOverlay\ (and MyOverlay-win64.zip) - the shareable launcher.
#
# Run from the repo root:  powershell -File packaging\build_exe.ps1
# Requires: uv (deps come from the project venv), internet on first run
# (downloads MinGit and ffmpeg into packaging\vendor\, cached afterwards).
# Versions come from third_party_versions.json at the repo root - bump a pin
# there and the next build refreshes the vendored copy automatically.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$pack = Join-Path $root "packaging"
$vendor = Join-Path $pack "vendor"
New-Item -ItemType Directory -Force $vendor | Out-Null

. (Join-Path $pack "third_party.ps1")
$pins = Get-ThirdPartyPins

# --- MinGit (portable git, ~45 MB) ---
$gitDir = Join-Path $vendor "git"
Install-PinnedTool -Name "MinGit" -Url $pins.git.url -Dir $gitDir -Stage {
    param($from, $to)
    # MinGit zips have no wrapper dir: cmd\, mingw64\, etc. sit at the root.
    Copy-Item (Join-Path $from "*") $to -Recurse -Force
}
Assert-PinnedVersion -Name "MinGit" -Pinned $pins.git.version `
    -Actual (Get-GitBinaryVersion (Join-Path $gitDir "cmd\git.exe"))

# --- ffmpeg (gyan.dev essentials build, ~90 MB) ---
$ffDir = Join-Path $vendor "ffmpeg"
Install-PinnedTool -Name "ffmpeg" -Url $pins.ffmpeg.url -Dir $ffDir -Stage {
    param($from, $to)
    $bin = Get-ChildItem -Recurse $from -Filter ffmpeg.exe | Select-Object -First 1
    if (-not $bin) { throw "ffmpeg.exe not found in the downloaded archive" }
    Copy-Item $bin.FullName $to
    Copy-Item (Join-Path $bin.DirectoryName "ffprobe.exe") $to
}
Assert-PinnedVersion -Name "ffmpeg" -Pinned $pins.ffmpeg.version `
    -Actual (Get-FfmpegBinaryVersion (Join-Path $ffDir "ffmpeg.exe"))

# --- build ---
Set-Location $root
uv sync
uv pip install pyinstaller
Set-Location $pack
uv run pyinstaller --noconfirm --distpath (Join-Path $root "dist") --workpath (Join-Path $pack "build") myoverlay.spec

# --- uv + mt into the payload root, so both are commands after an install
#     (and in the folder friends unzip). PyInstaller wipes the payload on
#     every build, so this has to run after it. See path_tools.ps1. ---
$distDir = Join-Path $root "dist\MyOverlay"
& (Join-Path $pack "path_tools.ps1") -Payload $distDir

# --- zip for sharing ---
$zip = Join-Path $root "dist\MyOverlay-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $distDir -DestinationPath $zip
Write-Host "done: $zip"
