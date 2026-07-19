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

# --- Google Cloud SDK Windows installer (bundled into the MSI) ---
$gcloud = Join-Path $vendor "gcloud\GoogleCloudSDKInstaller.exe"
if (-not (Test-Path $gcloud)) {
    Write-Host "Downloading Google Cloud SDK installer..."
    New-Item -ItemType Directory -Force (Split-Path $gcloud) | Out-Null
    Invoke-WebRequest -Uri "https://dl.google.com/dl/cloudsdk/channels/rapid/GoogleCloudSDKInstaller.exe" -OutFile $gcloud
}

New-Item -ItemType Directory -Force $build | Out-Null

# --- harvest the onedir payload ---
& (Join-Path $wix "heat.exe") dir $payload `
    -cg MyOverlayFiles -dr INSTALLFOLDER -srd -sreg -scom -gg `
    -var var.PayloadDir -out (Join-Path $build "HarvestedFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "heat failed" }

# --- compile ---
& (Join-Path $wix "candle.exe") -nologo -arch x64 "-dPayloadDir=$payload" "-dGCloudInstaller=$gcloud" `
    -ext WixUIExtension -out "$build\" `
    (Join-Path $msiDir "Product.wxs") `
    (Join-Path $msiDir "WizardUI.wxs") `
    (Join-Path $build "HarvestedFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "candle failed" }

# --- link ---
# ICE38/43/57/64: expected warnings for per-machine conditional shortcuts.
& (Join-Path $wix "light.exe") -nologo -ext WixUIExtension `
    -sice:ICE38 -sice:ICE43 -sice:ICE57 -sice:ICE64 -sice:ICE69 `
    -b $msiDir -out $out `
    (Join-Path $build "Product.wixobj") `
    (Join-Path $build "WizardUI.wixobj") `
    (Join-Path $build "HarvestedFiles.wixobj")
if ($LASTEXITCODE -ne 0) { throw "light failed" }

Write-Host "MSI ready: $out"
