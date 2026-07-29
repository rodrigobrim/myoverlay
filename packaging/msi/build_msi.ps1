# Build the MyOverlay MSI installer.
#
# Prereq: the PyInstaller onedir build must exist (run packaging\build_exe.ps1
# first -> dist\myoverlay\). This script then:
#   1. downloads the WiX 3.14 binaries into packaging\vendor\wix (once);
#   2. harvests dist\myoverlay into HarvestedFiles.wxs (heat);
#   3. compiles Product.wxs + WizardUI.wxs and links dist\myoverlay-setup.msi.
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\msi\build_msi.ps1
#         ... -Version 1.2.0     (release builds; CI passes the git tag)
#
# -Version becomes the MSI ProductVersion. It MUST change between releases:
# Product.wxs pairs `Product Id="*"` with a fixed UpgradeCode and
# <MajorUpgrade>, so two MSIs sharing a version never upgrade each other -
# the second install collides with the first instead of replacing it. The
# 0.1.0 default keeps local/dev builds behaving exactly as before.
param([string]$Version = "0.1.0")

$ErrorActionPreference = "Stop"
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version '$Version' must be MAJOR.MINOR.PATCH (MSI ProductVersion)."
}
$msiDir = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $msiDir "..\..")).Path
$payload = Join-Path $repo "dist\myoverlay"
$vendor = Join-Path $repo "packaging\vendor"
$wix = Join-Path $vendor "wix"
$build = Join-Path $repo "packaging\build\msi"
$out = Join-Path $repo "dist\myoverlay-setup.msi"

# Archive extraction that survives BOTH traps this build hits:
#
#   MAX_PATH - the Google Cloud SDK's deepest file lands ~268 characters in
#   from a normal checkout, past Windows' 260 limit, and long paths are off
#   by default (HKLM\...\FileSystem\LongPathsEnabled). Every path handed to
#   the filesystem is therefore \\?\-prefixed, which bypasses the limit
#   without touching a machine-wide setting.
#
#   Expand-Archive's rollback - on any failure it deletes what it already
#   wrote, and that rollback can itself throw, leaving a directory with a
#   few hundred of the SDK's ~30k files. install.bat survived exactly that,
#   which is why the cache check below counts files instead of trusting it.
#
# Extraction goes to a staging sibling and is moved into place only after it
# fully succeeds, so an interrupted build never leaves a half-extracted tree.
function Get-LongPath {
    param([string]$Path)
    # UNC paths take a different prefix; this build only ever uses local ones.
    if ($Path.StartsWith("\\?\")) { return $Path }
    return "\\?\" + $Path
}

function Remove-TreeLong {
    param([string]$Path)
    if ([string]::IsNullOrEmpty($Path)) { return }
    try {
        if ([System.IO.Directory]::Exists((Get-LongPath $Path))) {
            [System.IO.Directory]::Delete((Get-LongPath $Path), $true)
        }
    } catch {
        throw "could not remove $Path : $($_.Exception.Message)"
    }
}

function Expand-ZipTo {
    param([string]$Zip, [string]$Destination)

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $staging = "$Destination.stage"
    Remove-TreeLong $staging
    [System.IO.Directory]::CreateDirectory((Get-LongPath $staging)) | Out-Null

    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
        foreach ($entry in $archive.Entries) {
            # A directory entry has an empty Name; files carry their own.
            $target = Join-Path $staging $entry.FullName.Replace("/", "\")
            if ([string]::IsNullOrEmpty($entry.Name)) {
                [System.IO.Directory]::CreateDirectory((Get-LongPath $target)) | Out-Null
                continue
            }
            $parent = [System.IO.Path]::GetDirectoryName($target)
            [System.IO.Directory]::CreateDirectory((Get-LongPath $parent)) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile(
                $entry, (Get-LongPath $target), $true)
        }
    } catch {
        if ($archive) { $archive.Dispose(); $archive = $null }
        Remove-TreeLong $staging
        throw "extracting $Zip failed: $($_.Exception.Message)"
    } finally {
        if ($archive) { $archive.Dispose() }
    }

    Remove-TreeLong $Destination
    [System.IO.Directory]::Move((Get-LongPath $staging), (Get-LongPath $Destination))
}

# The WiX 3.14 tools are .NET Framework programs with no long-path support:
# heat/light simply report a file as "cannot be found" when its path passes
# 260 characters. Extraction here is \\?\-safe, but that only gets the files
# onto disk - WiX still has to read them. The Google Cloud SDK's deepest
# entry sits ~172 characters below the repo root, so a checkout deeper than
# roughly 85 characters cannot be built at all. Fail now, with the fix,
# rather than after a 150 MB download and a full harvest.
$deepestRelative = 172
if ($repo.Length + $deepestRelative -gt 259) {
    throw ("Checkout path is too long to build an MSI: '$repo' " +
        "($($repo.Length) chars; the WiX tools cap total paths at 260 and the " +
        "bundled SDK needs $deepestRelative more). Build from a shorter path - " +
        "a junction needs no admin rights and no copying:`n" +
        "    cmd /c mklink /J C:\mob `"$repo`"`n" +
        "    powershell -File C:\mob\packaging\msi\build_msi.ps1")
}

$payloadExe = Join-Path $payload "myoverlay.exe"
if (-not (Test-Path $payloadExe)) {
    throw "Payload missing: $payloadExe - run packaging\build_exe.ps1 first."
}

# The launcher is FROZEN into the exe: unlike media_tools (which every install
# git-pulls), launcher fixes only reach users through a rebuild. Shipping a
# payload older than the launcher source silently ships those fixes' absence -
# that is how an MSI once installed the Google Cloud SDK next to an exe that
# knew nothing about it, leaving `google-setup` reporting "gcloud not found".
$launcherSrc = Join-Path $repo "packaging\myoverlay_launcher.py"
$specSrc = Join-Path $repo "packaging\myoverlay.spec"
$exeTime = (Get-Item $payloadExe).LastWriteTime
foreach ($src in @($launcherSrc, $specSrc)) {
    if (-not (Test-Path $src)) { continue }
    $srcTime = (Get-Item $src).LastWriteTime
    if ($srcTime -gt $exeTime) {
        throw ("Stale payload: $payloadExe ($exeTime) is older than $src ($srcTime). " +
               "Re-run packaging\build_exe.ps1 so the MSI ships the current launcher.")
    }
}

# --- WiX toolset (binaries zip, no install required) ---
if (-not (Test-Path (Join-Path $wix "candle.exe"))) {
    Write-Host "Downloading WiX 3.14 binaries..."
    New-Item -ItemType Directory -Force $vendor | Out-Null
    $zip = Join-Path $vendor "wix314-binaries.zip"
    Invoke-WebRequest -Uri "https://github.com/wixtoolset/wix3/releases/download/wix314rtm/wix314-binaries.zip" -OutFile $zip
    Expand-ZipTo -Zip $zip -Destination $wix
    Remove-Item $zip
}

# --- Google Cloud SDK, bundled OFFLINE (the full versioned archive with
#     bundled Python, ~150 MB extracted) so the install is self-contained and
#     truly silent via install.bat --quiet. The 267 KB online stub was NOT the
#     SDK - it downloaded it and ran its own wizard.
$gcloudDir = Join-Path $vendor "gcloud-sdk"          # holds google-cloud-sdk\
$gcloudSdk = Join-Path $gcloudDir "google-cloud-sdk"
# install.bat alone is NOT proof of a good cache: a failed extraction once
# left exactly that file plus a few hundred of the SDK's ~30k, which this
# guard would have accepted - bundling a hollow SDK into the MSI. Check the
# file count too, cheaply.
# -First N caps the count at N, so the comparison has to be -ge: with -gt the
# cache always looked incomplete and every build re-downloaded 150 MB.
$gcloudFileProbe = 20000
$gcloudCached = (Test-Path (Join-Path $gcloudSdk "install.bat")) -and
    ((Get-ChildItem $gcloudSdk -Recurse -File -Force -ErrorAction SilentlyContinue |
        Select-Object -First $gcloudFileProbe).Count -ge $gcloudFileProbe)
if (-not $gcloudCached) {
    if (Test-Path $gcloudDir) {
        Write-Host "Google Cloud SDK cache is incomplete - re-extracting."
        Remove-TreeLong $gcloudDir
    }
    Write-Host "Downloading the offline Google Cloud SDK archive (~150 MB)..."
    $zip = Join-Path $vendor "google-cloud-cli-windows.zip"
    if (-not (Test-Path $zip)) {
        New-Item -ItemType Directory -Force $vendor | Out-Null
        Invoke-WebRequest -UseBasicParsing -OutFile $zip `
            -Uri "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-windows-x86_64-bundled-python.zip"
    }
    Write-Host "Extracting..."
    Expand-ZipTo -Zip $zip -Destination $gcloudDir
    Remove-Item $zip
}
if (-not (Test-Path (Join-Path $gcloudSdk "install.bat"))) {
    throw "Google Cloud SDK extraction did not produce $gcloudSdk\install.bat"
}

New-Item -ItemType Directory -Force $build | Out-Null

# --- branded wizard bitmaps (banner.bmp / dialog.bmp) from the logo assets ---
Write-Host "Generating wizard bitmaps from the branding assets..."
uv run --project $repo python (Join-Path $msiDir "gen_bitmaps.py")
if ($LASTEXITCODE -ne 0) { throw "gen_bitmaps failed" }

# --- bundled-component versions: SINGLE SOURCE OF TRUTH is the actual
#     binaries that ship. Read fresh on every build, so when a vendored tool
#     is updated the wizard shows the new version automatically. The versions
#     are passed to candle and rendered on the component-selection page. ---
$ffmpegExe = Get-ChildItem -Path $payload -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
$gitExe = Get-ChildItem -Path $payload -Recurse -Filter git.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName

$ffmpegVersion = "unknown"
if ($ffmpegExe) {
    $line = (& $ffmpegExe -version 2>$null | Select-Object -First 1)
    if ($line -match 'ffmpeg version (\d+\.\d+(\.\d+)?)') { $ffmpegVersion = $Matches[1] }
    elseif ($line -match 'ffmpeg version (\S+)') { $ffmpegVersion = ($Matches[1] -split '-')[0] }
}
$gitVersion = "unknown"
if ($gitExe) {
    $line = (& $gitExe --version 2>$null | Select-Object -First 1)
    if ($line -match 'git version (\d+\.\d+\.\d+)') { $gitVersion = $Matches[1] }
}
$gcloudVersion = (Get-Content (Join-Path $gcloudSdk "VERSION") -ErrorAction SilentlyContinue |
    Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($gcloudVersion)) { $gcloudVersion = "unknown" }
Write-Host "Bundled versions -> ffmpeg $ffmpegVersion | git $gitVersion | gcloud $gcloudVersion"
Write-Host "Product version  -> $Version"

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
    "-dProductVersion=$Version" `
    "-dFfmpegVersion=$ffmpegVersion" "-dGitVersion=$gitVersion" "-dGcloudVersion=$gcloudVersion" `
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
