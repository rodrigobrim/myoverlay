# Google project — manual setup + authentication token

`mt google-setup` automates everything below (gcloud + browser automation).
When it can't run — a locked-down machine, a Google UI change, or you just
want to see what it does — this is the same procedure done by hand in the
Google Cloud Console, followed by the token authorization.

Two separate credentials are involved, and it matters which is which:

| File | What it is | How you get it |
|---|---|---|
| `client_secret.json` | The **OAuth client** — identifies the *app* ("myoverlay") to Google. Contains a client id + `GOCSPX-…` secret. | Created once in the Cloud Console (steps 1–4). |
| `google-token` | The **user token** — proves *you* allowed that app to manage your YouTube channel. Contains an access token + refresh token. | Produced by `MyOverlay google-auth` (step 5). |

Paths are set in `config.toml` under `[youtube]` (`client_secret_file`,
`token_file`); by default both live next to `config.toml`.

## 1. Create a project

1. Sign in at <https://console.cloud.google.com> with the Google account that
   owns the YouTube channel.
2. Project picker (top bar) → **New project**. Any name works ("myoverlay").
   Project ids are globally unique, so Google may append digits — that's fine.
3. Wait for creation, then make sure the new project is **selected** in the
   picker. Every step below is per-project.

## 2. Enable the YouTube Data API v3

**APIs & Services → Library** → search *YouTube Data API v3* → **Enable**.

(This is the only API the pipeline uses; uploads, thumbnails and playlist
inserts all go through it.)

## 3. Configure and publish the OAuth consent screen

**APIs & Services → OAuth consent screen** (Google now calls this
*Google Auth Platform → Branding/Audience*):

1. If asked, choose **External** as the user type (personal accounts have no
   organization, so Internal is unavailable).
2. Fill only the required fields: app name (e.g. `myoverlay`), your email as
   the support and developer contact. No logo, no extra scopes, no domains.
3. **Publish the app to production** (Audience → *Publish app*). This is the
   step people skip and regret: while the consent screen is in *Testing*,
   Google expires every refresh token after **7 days**, and unattended
   uploads silently break weekly. "Needs verification" warnings are fine —
   an unverified production app just shows a scary consent page to you, the
   only user.

## 4. Create the Desktop OAuth client and save its JSON

**APIs & Services → Credentials → Create credentials → OAuth client ID**:

1. Application type: **Desktop app**. Name: anything.
2. On the creation dialog, click **Download JSON** immediately.
   **The secret is shown only this once** — after you close the dialog,
   Google no longer lets you view or download it. If you missed it, delete
   the client and create a new one; there is no recovery.
3. Save the file as `client_secret.json` at the path configured in
   `config.toml` (`youtube.client_secret_file`, default: next to
   `config.toml`).

The file should look like:

```json
{"installed": {"client_id": "1234…apps.googleusercontent.com",
               "client_secret": "GOCSPX-…",
               "auth_uri": "https://accounts.google.com/o/oauth2/auth",
               "token_uri": "https://oauth2.googleapis.com/token", …}}
```

An `{"installed": …}` top-level key is what the pipeline expects (that's what
"Desktop app" produces; a `{"web": …}` client won't work).

## 5. Authorize and get the token

```bash
MyOverlay google-auth
```

(dev checkout: `uv run mt google-auth`)

A browser window opens on Google's consent page. Sign in with the channel's
account and click **Allow** — the consent lists the single scope the pipeline
requests, `https://www.googleapis.com/auth/youtube` (manage your YouTube
account). The command then writes `google-token` and prints where it saved it.

What's in the token, and how it's used afterwards:

- The **refresh token** is the part that matters: it does not expire (with the
  consent screen in production) and lets the watcher upload unattended
  forever. The short-lived access token inside is refreshed automatically on
  every run; `google-token` is rewritten in place when that happens.
- The token is **bound to the OAuth client** that issued it. If you ever
  recreate the client (or the project), the old `google-token` is dead even
  though it still parses — the pipeline detects this mismatch and
  re-authorizes instead of failing mid-upload.
- Treat both files as secrets: anyone holding `client_secret.json` +
  `google-token` can manage the channel. Neither is committed to git.

After this, `MyOverlay publish` (or `MyOverlay run --publish`) uploads with no
further prompts.

## Troubleshooting

- **"no OAuth client secret at …"** — `client_secret.json` isn't at the
  configured path; redo step 4 (remember: new client, the old secret is
  unrecoverable).
- **`google-auth` says "no refresh token returned"** — Google only issues a
  refresh token on the first grant. Revoke the app's old grant at
  <https://myaccount.google.com/permissions>, delete `google-token`, re-run.
- **Uploads stop working after ~a week** — consent screen still in *Testing*;
  publish it to production (step 3) and re-authorize.
- **Browser refuses sign-in ("This browser or app may not be secure")** — you
  are inside an automation-controlled browser; do the authorization in a
  normal browser window.
- **Uploads land private/locked** — expected: new projects' uploads stay
  locked until Google's API audit clears, regardless of the configured
  privacy. Not a setup error.
