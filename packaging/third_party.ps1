# Shared plumbing for the pinned third-party tools that ship with MyOverlay.
# Dot-source this file, then call the functions below.
#
# third_party_versions.json (repo root) is the single source of truth for
# WHICH version of each vendored tool is downloaded. Consumers:
#   - packaging\build_exe.ps1           (ffmpeg, MinGit)
#   - packaging\path_tools.ps1          (uv)
#   - packaging\msi\build_msi.ps1       (Google Cloud SDK + payload cross-check)
#   - .github\workflows\tests.yml       (ffmpeg + uv for the test suite)
#   - tests\test_third_party_versions.py (pin consistency + installed-tool checks)
#
# Cache invalidation: every vendored dir carries a .pinned-source marker
# holding the url it was downloaded from. A marker that does not match the
# json wipes the dir and re-downloads, so bumping a pin is all it takes to
# refresh a warm packaging\vendor cache - no manual deleting.

function Get-ThirdPartyPins {
    $path = Join-Path (Split-Path -Parent $PSScriptRoot) "third_party_versions.json"
    if (-not (Test-Path $path)) { throw "Pin file missing: $path" }
    Get-Content $path -Raw | ConvertFrom-Json
}

function Test-PinnedVendorDir {
    param([string]$Dir, [string]$Url)
    $marker = Join-Path $Dir ".pinned-source"
    if (-not (Test-Path $marker)) { return $false }
    (Get-Content $marker -Raw).Trim() -eq $Url
}

# Download url -> extracted temp dir, staging the zip so a dropped connection
# can never leave anything a cache check would mistake for a complete download.
function Expand-PinnedZip {
    param([string]$Url)
    $tmpZip = Join-Path $env:TEMP ([IO.Path]::GetRandomFileName() + ".zip")
    $part = "$tmpZip.part"
    Write-Host "downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $part -UseBasicParsing
    Move-Item $part $tmpZip -Force
    $tmpDir = Join-Path $env:TEMP ("tp_" + [IO.Path]::GetRandomFileName())
    Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force
    Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
    return $tmpDir
}

# Ensures $Dir holds exactly what $Url provides. $Stage receives the extracted
# archive root and the (fresh, empty) $Dir and copies what should be kept.
# No-op when the marker already matches; stale versions are wiped first.
function Install-PinnedTool {
    param([string]$Name, [string]$Url, [string]$Dir, [scriptblock]$Stage)
    if (Test-PinnedVendorDir -Dir $Dir -Url $Url) { return }
    if (Test-Path $Dir) {
        Write-Host "vendored $Name is not at the pinned version - refreshing"
        Remove-Item -Recurse -Force $Dir
    }
    $tmpDir = Expand-PinnedZip -Url $Url
    try {
        New-Item -ItemType Directory -Force $Dir | Out-Null
        & $Stage $tmpDir $Dir
        Set-Content -Path (Join-Path $Dir ".pinned-source") -Value $Url -Encoding Ascii
    } catch {
        # Never leave a half-staged dir that a later run would trust.
        Remove-Item -Recurse -Force $Dir -ErrorAction SilentlyContinue
        throw
    } finally {
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
    }
}

# The downloaded binary must actually BE the pinned version - a moved
# release asset or a stale mirror fails the build here, not on a user's
# machine. $Actual is the version parsed from the binary's own output.
function Assert-PinnedVersion {
    param([string]$Name, [string]$Actual, [string]$Pinned)
    if ($Actual -ne $Pinned) {
        throw ("$Name reports version '$Actual' but third_party_versions.json pins " +
               "'$Pinned'. Fix the pin (version + url must agree) and rebuild.")
    }
}

function Get-FfmpegBinaryVersion {
    param([string]$FfmpegExe)
    $line = (& $FfmpegExe -version 2>$null | Select-Object -First 1)
    if ($line -match 'ffmpeg version (\S+)') { return ($Matches[1] -split '-')[0] }
    return "unknown"
}

function Get-GitBinaryVersion {
    param([string]$GitExe)
    # "git version 2.55.0.windows.3" -> 2.55.0 (the .windows.N re-spin suffix
    # is pinned by the url, not the display version).
    $line = (& $GitExe --version 2>$null | Select-Object -First 1)
    if ($line -match 'git version (\d+\.\d+\.\d+)') { return $Matches[1] }
    return "unknown"
}

function Get-UvBinaryVersion {
    param([string]$UvExe)
    # "uv 0.12.1 (hash date)" -> 0.12.1
    $line = (& $UvExe --version 2>$null | Select-Object -First 1)
    if ($line -match '^uv (\S+)') { return $Matches[1] }
    return "unknown"
}
