# Build the MyOverlay MSI installer.
#
# Prereq: the PyInstaller onedir build must exist (run packaging\build_exe.ps1
# first -> dist\myoverlay\). This script then:
#   1. downloads the WiX 3.14 binaries into packaging\vendor\wix (once);
#   2. harvests dist\myoverlay into HarvestedFiles.wxs (heat);
#   3. compiles Product.wxs + WizardUI.wxs and links dist\myoverlay-setup.msi.
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\msi\build_msi.ps1

$ErrorActionPreference = "Stop"
$msiDir = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $msiDir "..\..")).Path
$payload = Join-Path $repo "dist\myoverlay"
$vendor = Join-Path $repo "packaging\vendor"
$wix = Join-Path $vendor "wix"
$build = Join-Path $repo "packaging\build\msi"
$out = Join-Path $repo "dist\myoverlay-setup.msi"

if (-not (Test-Path (Join-Path $payload "myoverlay.exe"))) {
    throw "Payload missing: $payload\myoverlay.exe - run packaging\build_exe.ps1 first."
}

# --- WiX toolset (binaries zip, no install required) ---
if (-not (Test-Path (Join-Path $wix "candle.exe"))) {
    Write-Host "Downloading WiX 3.14 binaries..."
    New-Item -ItemType Directory -Force $vendor | Out-Null
    $zip = Join-Path $vendor "wix314-binaries.zip"
    Invoke-WebRequest -Uri "https://github.com/wixtoolset/wix3/releases/download/wix314rtm/wix314-binaries.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $wix -Force
    Remove-Item $zip
}

# --- Google Cloud SDK, bundled OFFLINE (the full versioned archive with
#     bundled Python, ~150 MB extracted) so the install is self-contained and
#     truly silent via install.bat --quiet. The 267 KB online stub was NOT the
#     SDK - it downloaded it and ran its own wizard.
$gcloudDir = Join-Path $vendor "gcloud-sdk"          # holds google-cloud-sdk\
$gcloudSdk = Join-Path $gcloudDir "google-cloud-sdk"
if (-not (Test-Path (Join-Path $gcloudSdk "install.bat"))) {
    Write-Host "Downloading the offline Google Cloud SDK archive (~150 MB)..."
    New-Item -ItemType Directory -Force $gcloudDir | Out-Null
    $zip = Join-Path $vendor "google-cloud-cli-windows.zip"
    Invoke-WebRequest -UseBasicParsing -OutFile $zip `
        -Uri "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64-bundled-python.zip"
    Write-Host "Extracting..."
    Expand-Archive -Path $zip -DestinationPath $gcloudDir -Force
    Remove-Item $zip
}

New-Item -ItemType Directory -Force $build | Out-Null

# --- harvest the onedir payload ---
& (Join-Path $wix "heat.exe") dir $payload `
    -cg MyOverlayFiles -dr INSTALLFOLDER -srd -sreg -scom -gg `
    -var var.PayloadDir -out (Join-Path $build "HarvestedFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "heat failed" }

# --- harvest the offline SDK into its own component group / feature ---
& (Join-Path $wix "heat.exe") dir $gcloudSdk `
    -cg GCloudFiles -dr GCLOUDDIR -srd -sreg -scom -gg `
    -var var.GCloudDir -out (Join-Path $build "GCloudFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "heat (gcloud) failed" }

# --- compile ---
& (Join-Path $wix "candle.exe") -nologo -arch x64 "-dPayloadDir=$payload" "-dGCloudDir=$gcloudSdk" `
    -ext WixUIExtension -out "$build\" `
    (Join-Path $msiDir "Product.wxs") `
    (Join-Path $msiDir "WizardUI.wxs") `
    (Join-Path $build "HarvestedFiles.wxs") `
    (Join-Path $build "GCloudFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "candle failed" }

# --- link ---
# ICE38/43/57/64: expected warnings for per-machine conditional shortcuts.
& (Join-Path $wix "light.exe") -nologo -ext WixUIExtension `
    -sice:ICE20 -sice:ICE38 -sice:ICE43 -sice:ICE57 -sice:ICE60 -sice:ICE64 -sice:ICE69 `
    -b $msiDir -out $out `
    (Join-Path $build "Product.wixobj") `
    (Join-Path $build "WizardUI.wixobj") `
    (Join-Path $build "HarvestedFiles.wixobj") `
    (Join-Path $build "GCloudFiles.wixobj")
if ($LASTEXITCODE -ne 0) { throw "light failed" }

Write-Host "MSI ready: $out"
