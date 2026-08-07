# Build the MyOverlay MSI installer.
#
# Prereq: the PyInstaller onedir build must exist (run packaging\build_exe.ps1
# first -> dist\MyOverlay\). This script then:
#   1. downloads the WiX 3.14 binaries into packaging\vendor\wix (once);
#   2. harvests dist\MyOverlay into HarvestedFiles.wxs (heat);
#   3. compiles Product.wxs + WizardUI.wxs and links dist\MyOverlay-setup.msi.
#
# Usage:  powershell -ExecutionPolicy Bypass -File packaging\msi\build_msi.ps1
#         ... -Version 0.7.1234   (release builds; CI derives this from the
#                                  date tag - see packaging\version.ps1)
#
# -Version becomes the MSI ProductVersion. It MUST change between builds that
# will be installed: Product.wxs pairs `Product Id="*"` with a fixed
# UpgradeCode and <MajorUpgrade>, so two MSIs sharing a version never upgrade
# each other - the second install collides with the first instead of
# replacing it (observed live: "Setup Wizard ended prematurely"). Local
# builds default to a monotonic date-derived version (packaging\version.ps1 -
# same scheme tagged releases use) so every rebuild cleanly MajorUpgrades the
# previous install. Two builds in the same minute still collide - pass
# -Version explicitly then.
param([string]$Version = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\version.ps1")
if (-not $Version) {
    $Version = ConvertTo-MsiVersion
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version '$Version' must be MAJOR.MINOR.PATCH (MSI ProductVersion)."
}
$msiDir = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $msiDir "..\..")).Path
$payload = Join-Path $repo "dist\MyOverlay"
$vendor = Join-Path $repo "packaging\vendor"
$wix = Join-Path $vendor "wix"
$build = Join-Path $repo "packaging\build\msi"
$out = Join-Path $repo "dist\MyOverlay-setup.msi"

. (Join-Path $repo "packaging\third_party.ps1")
$pins = Get-ThirdPartyPins

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

$payloadExe = Join-Path $payload "MyOverlay.exe"
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

# --- uv + mt into the payload root. build_exe.ps1 already stages them, but
#     they are re-staged here so a payload built before this existed (or one
#     whose mt.cmd changed) still ships them without a full exe rebuild: they
#     are plain files beside the exe, not part of the frozen bundle. heat
#     harvests the payload root into INSTALLFOLDER, which the AppPath
#     component puts on the machine PATH - that is what makes `uv` and `mt`
#     commands after an install, resolved from the install directory. ---
& (Join-Path $repo "packaging\path_tools.ps1") -Payload $payload

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
#     bundled Python) so the install is self-contained - no download, no
#     wizard. The 267 KB online stub was NOT the SDK; it downloaded it and ran
#     its own wizard.
#
# Google's archive ships INTO the MSI as a single file and is expanded on the
# target machine (see ExpandGCloud in gcloud_payload.js). It is deliberately
# never extracted here:
#
#   * The SDK's deepest entry sits 189 characters below the repo root. The
#     WiX 3.14 tools are .NET Framework programs with no long-path support,
#     so heat reports such a file as "cannot be found" and the build dies -
#     from any checkout deeper than ~70 characters. Installed, the same file
#     is only ~189 characters from the drive root, comfortably inside the
#     limit, so expanding on the target is the one place the path is short.
#   * It also removes ~30k files from the harvest, which is most of the MSI
#     build time.
$gcloudZip = Join-Path $vendor "google-cloud-cli-windows.zip"

# A truncated download would ship as a corrupt payload that only fails on the
# user's machine, so the archive is opened (not extracted) and checked. An
# unusable archive is deleted and $null returned, so callers can re-download.
function Read-GcloudArchiveVersion {
    param([string]$Zip)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Zip)
        $versionEntry = $archive.GetEntry("google-cloud-sdk/VERSION")
        if (-not $archive.GetEntry("google-cloud-sdk/install.bat") -or -not $versionEntry) {
            throw "google-cloud-sdk/install.bat or /VERSION missing"
        }
        $reader = New-Object System.IO.StreamReader($versionEntry.Open())
        $v = $reader.ReadLine()
        $reader.Close()
        if ([string]::IsNullOrWhiteSpace($v)) { throw "VERSION file is empty" }
        return $v.Trim()
    } catch {
        if ($archive) { $archive.Dispose(); $archive = $null }
        Write-Host "The Google Cloud SDK archive is not usable ($($_.Exception.Message)) - deleting it."
        Remove-Item $Zip -Force -ErrorAction SilentlyContinue
        return $null
    } finally {
        if ($archive) { $archive.Dispose() }
    }
}

# Google publishes no versioned bundled-python archive, only this rolling
# rapid-channel url, so the pin is enforced by checking the VERSION file
# inside the archive instead of by the download url.
if (Test-Path $gcloudZip) {
    $cached = Read-GcloudArchiveVersion $gcloudZip
    if ($cached -and $cached -ne $pins.gcloud.version) {
        Write-Host "Cached Google Cloud SDK archive is $cached, pin is $($pins.gcloud.version) - re-downloading."
        Remove-Item $gcloudZip -Force
    }
}
if (-not (Test-Path $gcloudZip)) {
    New-Item -ItemType Directory -Force $vendor | Out-Null
    # Download to a staging name and rename only on success: a connection
    # dropped mid-transfer (which happens on this 110 MB file) would otherwise
    # leave a truncated archive that the Test-Path above reads as "cached".
    # Retried, because one reset connection should not fail a whole build.
    $part = "$gcloudZip.part"
    $uri = $pins.gcloud.url
    $attempts = 3
    for ($try = 1; $try -le $attempts; $try++) {
        Write-Host "Downloading the offline Google Cloud SDK archive (~110 MB), attempt $try/$attempts..."
        Remove-Item $part -Force -ErrorAction SilentlyContinue
        try {
            Invoke-WebRequest -UseBasicParsing -OutFile $part -Uri $uri
            Move-Item $part $gcloudZip -Force
            break
        } catch {
            Write-Host "  download failed: $($_.Exception.Message)"
            Remove-Item $part -Force -ErrorAction SilentlyContinue
            if ($try -eq $attempts) {
                throw "Could not download the Google Cloud SDK archive after $attempts attempts."
            }
            Start-Sleep -Seconds (5 * $try)
        }
    }
}
$gcloudVersion = Read-GcloudArchiveVersion $gcloudZip
if (-not $gcloudVersion) {
    throw "The downloaded Google Cloud SDK archive was not usable - re-run the build to download it again."
}
if ($gcloudVersion -ne $pins.gcloud.version) {
    throw ("Google's rapid channel now serves Cloud SDK $gcloudVersion but third_party_versions.json " +
        "pins $($pins.gcloud.version). There is no versioned bundled-python archive to fall back to, " +
        "so review the SDK release notes and bump the gcloud pin to $gcloudVersion, then rebuild. " +
        "(The $gcloudVersion archive stays in packaging\vendor - once the pin is bumped the " +
        "rebuild reuses it without another download.)")
}

New-Item -ItemType Directory -Force $build | Out-Null

# --- branded wizard bitmaps (banner.bmp / dialog.bmp) from the logo assets ---
Write-Host "Generating wizard bitmaps from the branding assets..."
uv run --project $repo python (Join-Path $msiDir "gen_bitmaps.py")
if ($LASTEXITCODE -ne 0) { throw "gen_bitmaps failed" }

# --- wizard resolution combo: generated from src\media_tools\resolutions.json,
#     the single source of truth also feeding config.py, the CLI help, the GUI
#     and docs/CLI.md, so the installer can never drift from the app.
#     WizardUI.wxs pulls the items in via <?include resolutions.wxi ?>. ---
$resolutions = Get-Content (Join-Path $repo "src\media_tools\resolutions.json") -Raw | ConvertFrom-Json
$wxiLines = @(
    '<?xml version="1.0" encoding="utf-8"?>'
    '<!-- Generated by build_msi.ps1 from src\media_tools\resolutions.json. Do not edit. -->'
    '<Include>'
    '  <ComboBox Property="RESOLUTION">'
)
foreach ($preset in $resolutions.PSObject.Properties) {
    $wxiLines += "    <ListItem Text=`"$($preset.Name) ($($preset.Value)p)`" Value=`"$($preset.Name)`" />"
}
$wxiLines += '  </ComboBox>'
$wxiLines += '</Include>'
Set-Content -Path (Join-Path $build "resolutions.wxi") -Value $wxiLines -Encoding utf8

# --- bundled-component versions: the wizard shows what the actual binaries
#     in the payload report, read fresh on every build - and the build fails
#     if that disagrees with third_party_versions.json, which catches a
#     payload built before a pin was bumped (fix: re-run build_exe.ps1). ---
$ffmpegExe = Get-ChildItem -Path $payload -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
$gitExe = Get-ChildItem -Path $payload -Recurse -Filter git.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName

$ffmpegVersion = "unknown"
if ($ffmpegExe) { $ffmpegVersion = Get-FfmpegBinaryVersion $ffmpegExe }
$gitVersion = "unknown"
if ($gitExe) { $gitVersion = Get-GitBinaryVersion $gitExe }
Write-Host "Bundled versions -> ffmpeg $ffmpegVersion | git $gitVersion | gcloud $gcloudVersion"
Assert-PinnedVersion -Name "payload ffmpeg" -Actual $ffmpegVersion -Pinned $pins.ffmpeg.version
Assert-PinnedVersion -Name "payload git" -Actual $gitVersion -Pinned $pins.git.version
Write-Host "Product version  -> $Version"

# The WiX 3.14 tools are .NET Framework programs with no long-path support:
# heat/light report a file whose path passes 260 characters as "cannot be
# found" and the build dies. Nothing here can widen that, so check the actual
# tree about to be harvested and fail with the fix instead of a puzzle. This
# measures rather than assuming a worst case: the bundled Cloud SDK used to
# blow the limit from any normal checkout, which is exactly why it now ships
# as an archive expanded on the target machine.
$longest = Get-ChildItem $payload -Recurse -File -Force -ErrorAction SilentlyContinue |
    Sort-Object { $_.FullName.Length } -Descending | Select-Object -First 1
if ($longest -and $longest.FullName.Length -gt 259) {
    throw ("Payload paths are too long for the WiX tools (limit 260): " +
        "$($longest.FullName.Length) chars at`n    $($longest.FullName)`n" +
        "Build from a shorter directory - a junction needs no admin rights " +
        "and no copying:`n" +
        "    cmd /c mklink /J C:\mob `"$repo`"`n" +
        "    powershell -File C:\mob\packaging\msi\build_msi.ps1")
}

# --- harvest the onedir payload ---
& (Join-Path $wix "heat.exe") dir $payload `
    -cg MyOverlayFiles -dr INSTALLFOLDER -srd -sreg -scom -gg `
    -var var.PayloadDir -out (Join-Path $build "HarvestedFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "heat failed" }

# The SDK is NOT harvested - it ships as the single archive file referenced
# by var.GCloudZip and is expanded on the target machine.

# --- compile ---
& (Join-Path $wix "candle.exe") -nologo -arch x64 "-dPayloadDir=$payload" "-dGCloudZip=$gcloudZip" `
    "-dProductVersion=$Version" `
    "-dFfmpegVersion=$ffmpegVersion" "-dGitVersion=$gitVersion" "-dGcloudVersion=$gcloudVersion" `
    "-I$build" `
    -ext WixUIExtension -out "$build\" `
    (Join-Path $msiDir "Product.wxs") `
    (Join-Path $msiDir "WizardUI.wxs") `
    (Join-Path $build "HarvestedFiles.wxs")
if ($LASTEXITCODE -ne 0) { throw "candle failed" }

# --- link ---
# ICE38/43/57/64: expected warnings for per-machine conditional shortcuts.
& (Join-Path $wix "light.exe") -nologo -ext WixUIExtension `
    -sice:ICE20 -sice:ICE38 -sice:ICE43 -sice:ICE57 -sice:ICE60 -sice:ICE64 -sice:ICE69 `
    -b $msiDir -out $out `
    (Join-Path $build "Product.wixobj") `
    (Join-Path $build "WizardUI.wixobj") `
    (Join-Path $build "HarvestedFiles.wixobj")
if ($LASTEXITCODE -ne 0) { throw "light failed" }

Write-Host "MSI ready: $out"
