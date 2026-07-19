<#
.SYNOPSIS
  Runs every automatable step of the YouTube Data API setup for `mt publish`,
  then prints direct links for the steps Google exposes no API for.

  Automated here:
    1. create (or reuse) a Google Cloud project
    2. enable the YouTube Data API v3

  Manual (no API exists — links printed at the end):
    - configure + PUBLISH the OAuth consent screen
    - create the Desktop OAuth client + download client_secret.json
    - one-time interactive authorization (`mt publish` once)

  Idempotent: safe to re-run. The links always print, even if the project
  already exists or a step is skipped.

.PARAMETER ProjectId
  Cloud project id to create/reuse. Defaults to a unique generated id.

.EXAMPLE
  scripts\gcloud_youtube_setup.ps1
  scripts\gcloud_youtube_setup.ps1 -ProjectId media-tools-yt-prod
#>
[CmdletBinding()]
param(
  [string]$ProjectId = "media-tools-yt-$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())"
)

# NOTE: deliberately NOT 'Stop'. gcloud runs through a PowerShell wrapper
# (gcloud.ps1) whose internal python stderr becomes a *terminating* error under
# 'Stop' — which would abort the "does this project exist?" probe (that probe is
# EXPECTED to fail when the project doesn't exist yet). We check $LASTEXITCODE.
$ErrorActionPreference = 'Continue'

function Write-ManualLinks($Project) {
  Write-Host ""
  Write-Host "============================================================"
  Write-Host "Project: $Project"
  Write-Host ""
  Write-Host "MANUAL steps remaining (no API exists) - open in this order:"
  Write-Host ""
  Write-Host "1. Configure + PUBLISH the OAuth consent screen (External, then"
  Write-Host "   click 'PUBLISH APP' so refresh tokens don't expire after 7 days):"
  Write-Host "   https://console.cloud.google.com/auth/overview?project=$Project"
  Write-Host ""
  Write-Host "2. Create an OAuth client ID (application type: Desktop app) and"
  Write-Host "   download its JSON -> save as client_secret.json in the working dir:"
  Write-Host "   https://console.cloud.google.com/apis/credentials?project=$Project"
  Write-Host ""
  Write-Host "3. Authorize once (opens a browser, click Allow). After this the"
  Write-Host "   refresh token in token.json keeps every future upload unattended:"
  Write-Host "   mt publish"
  Write-Host "============================================================"
}

# --- preconditions --------------------------------------------------------
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
  throw "gcloud not found. Install the Cloud SDK: https://cloud.google.com/sdk/docs/install"
}

$account = (gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>$null | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($account)) {
  throw "No active gcloud account. Run: gcloud auth login"
}
Write-Host ">> Authenticated as: $account"

# --- automated steps (idempotent) -----------------------------------------
try {
  # 1. project: reuse if it already exists, else create
  gcloud projects describe $ProjectId 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host ">> Project '$ProjectId' already exists - reusing"
  } else {
    Write-Host ">> Creating project: $ProjectId"
    gcloud projects create $ProjectId --name="media-tools YouTube" --set-as-default
    if ($LASTEXITCODE -ne 0) { throw "project create failed (exit $LASTEXITCODE)" }
  }

  # 2. enable the YouTube Data API v3 (free tier - no billing account required)
  Write-Host ">> Enabling YouTube Data API v3"
  gcloud services enable youtube.googleapis.com --project=$ProjectId
  if ($LASTEXITCODE -ne 0) { throw "enable API failed (exit $LASTEXITCODE)" }
  Write-Host ">> Automated steps complete."
}
catch {
  Write-Host "error: $_"
}
finally {
  # always surface the manual links, even if a step above failed
  Write-ManualLinks $ProjectId
}
