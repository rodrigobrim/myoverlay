# One-command idempotent build: exe payload + MSI installer.
#
# Each stage runs only when its output is missing or older than its inputs,
# so re-running after a no-op change costs seconds, not minutes:
#   exe  (packaging\build_exe.ps1)      rebuilt when dist\MyOverlay\MyOverlay.exe
#        is missing or older than the frozen launcher sources (launcher .py,
#        .spec, build_exe.ps1 itself). media_tools is NOT frozen - installs
#        git-pull it - so app-code edits never require an exe rebuild.
#   MSI  (packaging\msi\build_msi.ps1)  rebuilt when dist\MyOverlay-setup.msi
#        is missing, the exe was just rebuilt, or any MSI source is newer
#        (packaging\msi\*, wizard data such as src\media_tools\resolutions.json,
#        the config.toml template).
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#         ... -ForceExe          rebuild the exe payload even if fresh
#         ... -ForceMsi          rebuild the MSI even if fresh
#         ... -Force             rebuild both
#         ... -Version 1.2.0    forwarded to build_msi.ps1 (release builds)
param(
    [switch]$ForceExe,
    [switch]$ForceMsi,
    [switch]$Force,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
if ($Force) { $ForceExe = $true; $ForceMsi = $true }

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$payloadExe = Join-Path $repo "dist\MyOverlay\MyOverlay.exe"
$msi = Join-Path $repo "dist\MyOverlay-setup.msi"

function Get-NewestWriteTime {
    param([string[]]$Paths)
    $newest = [DateTime]::MinValue
    foreach ($p in $Paths) {
        if (-not (Test-Path $p)) { continue }
        foreach ($item in @(Get-Item $p) + @(Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue)) {
            if (-not $item.PSIsContainer -and $item.LastWriteTime -gt $newest) {
                $newest = $item.LastWriteTime
            }
        }
    }
    return $newest
}

# --- exe payload ---
$exeInputs = @(
    (Join-Path $repo "packaging\myoverlay_launcher.py"),
    (Join-Path $repo "packaging\myoverlay.spec"),
    (Join-Path $repo "packaging\build_exe.ps1")
)
$buildExe = $ForceExe -or -not (Test-Path $payloadExe)
if (-not $buildExe) {
    $buildExe = (Get-NewestWriteTime $exeInputs) -gt (Get-Item $payloadExe).LastWriteTime
}
if ($buildExe) {
    Write-Host "== exe payload: building =="
    & (Join-Path $repo "packaging\build_exe.ps1")
} else {
    Write-Host "== exe payload: fresh, skipping (-ForceExe to rebuild) =="
}

# --- MSI ---
# packaging\msi covers the wxs/js sources and build_msi.ps1 itself; the
# generated wizard bitmaps land there too, but they are written before the
# MSI is linked, so they never retrigger a build on their own.
$msiInputs = @(
    $payloadExe,
    (Join-Path $repo "packaging\msi"),
    (Join-Path $repo "packaging\third_party.ps1"),
    (Join-Path $repo "third_party_versions.json"),
    (Join-Path $repo "src\media_tools\resolutions.json"),
    (Join-Path $repo "config.toml")
)
$buildMsi = $ForceMsi -or $buildExe -or -not (Test-Path $msi)
if (-not $buildMsi) {
    $buildMsi = (Get-NewestWriteTime $msiInputs) -gt (Get-Item $msi).LastWriteTime
}
if ($buildMsi) {
    Write-Host "== MSI: building =="
    if ($Version) {
        & (Join-Path $repo "packaging\msi\build_msi.ps1") -Version $Version
    } else {
        & (Join-Path $repo "packaging\msi\build_msi.ps1")
    }
} else {
    Write-Host "== MSI: fresh, skipping (-ForceMsi to rebuild) =="
}

Write-Host "Done. MSI: $msi"
