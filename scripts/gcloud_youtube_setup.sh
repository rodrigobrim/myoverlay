#!/usr/bin/env bash
#
# Runs every automatable step of the YouTube Data API setup for `mt publish`,
# then prints direct links for the steps Google exposes no API for.
#
# Automated here:
#   1. create (or reuse) a Google Cloud project
#   2. enable the YouTube Data API v3
#
# Manual (no API exists — links printed at the end):
#   - configure / publish the OAuth consent screen
#   - create the Desktop OAuth client + download client_secret.json
#   - the one-time interactive "Allow" authorization (run `mt publish` once)
#
# Idempotent: safe to re-run. The links always print, even if the project
# already exists or a step is skipped.
#
# Prereqs: gcloud CLI installed and `gcloud auth login` already done.
#
# Usage:
#   scripts/gcloud_youtube_setup.sh [PROJECT_ID]

set -uo pipefail

command -v gcloud >/dev/null 2>&1 || {
  echo "error: gcloud not found. Install the Cloud SDK: https://cloud.google.com/sdk/docs/install" >&2
  exit 1
}

if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
  echo "error: no active gcloud account. Run: gcloud auth login" >&2
  exit 1
fi

PROJECT_ID="${1:-media-tools-yt-$(date +%s)}"

print_links() {
  cat <<EOF

============================================================
Project: $PROJECT_ID

MANUAL steps remaining (no API exists) — open in this order:

1. Configure + PUBLISH the OAuth consent screen (External, then click
   "PUBLISH APP" so refresh tokens don't expire after 7 days):
   https://console.cloud.google.com/auth/overview?project=$PROJECT_ID

2. Create an OAuth client ID (application type: Desktop app) and download
   its JSON — save it as client_secret.json in the media-tools working dir:
   https://console.cloud.google.com/apis/credentials?project=$PROJECT_ID

3. Authorize once (opens a browser, click Allow). After this the refresh
   token in token.json keeps every future upload unattended:
   mt publish
============================================================
EOF
}
# always surface the manual links, even if a step below fails
trap print_links EXIT

# 1. project: reuse if it already exists, else create
if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
  echo ">> Project '$PROJECT_ID' already exists — reusing"
else
  echo ">> Creating project: $PROJECT_ID"
  gcloud projects create "$PROJECT_ID" --name="media-tools YouTube" --set-as-default
fi

# 2. enable the YouTube Data API v3 (free tier — no billing account required)
echo ">> Enabling YouTube Data API v3"
gcloud services enable youtube.googleapis.com --project="$PROJECT_ID"
