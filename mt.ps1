# `mt` for THIS checkout: run the CLI from the working copy this script sits
# in, including uncommitted edits, against the real library and credentials.
#
#     .\mt meta 2026-07-30
#     .\mt publish 2026-07-30 --show-published
#
# The file is tracked, so every clone and every worktree has it at its root and
# `.\mt` always means "the branch I am standing in" - no PATH edits, no profile
# function, and the installed `mt` keeps behaving exactly as it does today.
#
# It reproduces the environment MyOverlay.exe sets up in managed mode:
#   * working directory ~\MyOverlay, so config.toml, google-token and
#     client_secret.json resolve to the real ones (never a copy in the checkout)
#   * MYOVERLAY_FFMPEG_DIR / PATH pointing at the INSTALLED bundled binaries,
#     so ffmpeg, ffprobe and git are the same ones the exe runs
# Only the Python code differs: it comes from this checkout instead of the
# managed clone. Once a change is merged, validate it again through plain `mt`.
#
# Why not `mt --branch <name>`: that checks the branch out in the managed clone
# at ~\MyOverlay\repo, which git refuses when a worktree already holds it.

$ErrorActionPreference = 'Stop'

$repo = (& git -C $PSScriptRoot rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $repo) {
    Write-Host "ERROR: $PSScriptRoot is not a git working copy." -ForegroundColor Red
    exit 2
}
$repo = $repo.Trim() -replace '/', '\'
if (-not (Test-Path (Join-Path $repo 'src\media_tools'))) {
    Write-Host "ERROR: $repo does not contain src\media_tools - wrong repository?" -ForegroundColor Red
    exit 2
}

$install = 'C:\Program Files\MyOverlay'
$launcher = (Get-Command 'mt.cmd' -CommandType Application -ErrorAction SilentlyContinue |
             Select-Object -First 1).Source
if ($launcher) { $install = Split-Path $launcher -Parent }
$ffmpegDir = Join-Path $install '_internal\ffmpeg'
$gitDir    = Join-Path $install '_internal\git\cmd'
$gcloudBin = Join-Path $install 'google-cloud-sdk\bin'

$home_ = Join-Path $HOME 'MyOverlay'
if (-not (Test-Path (Join-Path $home_ 'config.toml'))) {
    Write-Host "ERROR: $home_\config.toml not found - is MyOverlay installed and run once?" -ForegroundColor Red
    exit 2
}

$branch = (& git -C $repo rev-parse --abbrev-ref HEAD).Trim()
Write-Host "[mt.ps1] $branch  <-  $repo" -ForegroundColor DarkGray

# Child-process environment only: this script sets nothing outside its own run.
if (Test-Path $ffmpegDir) { $env:MYOVERLAY_FFMPEG_DIR = $ffmpegDir }
if (Test-Path $gcloudBin) { $env:MYOVERLAY_GCLOUD_BIN = $gcloudBin }
$prepend = @($ffmpegDir, $gitDir, $gcloudBin) | Where-Object { Test-Path $_ }
$env:PATH = ($prepend + $env:PATH) -join ';'

# cwd decides where config.toml and the relative credential paths are read
# from; --project keeps the code (and its venv) coming from this checkout.
Push-Location $home_
try {
    & uv run --project $repo mt @args
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
