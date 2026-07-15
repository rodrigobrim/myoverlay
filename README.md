# media-tools

Zero-touch karting video pipeline: DJI Osmo Action footage + AiM MyChron
telemetry in, YouTube videos with a telemetry overlay out.

```
camera SD/USB ──┐
                ├─> ingest ─> correlate ─> sync ─> render ─> publish
Race Studio 3 ──┘              (sessions)  (audio↔RPM)  (overlay)  (YouTube)
```

Every stage records its work in a per-track-day `session.json` manifest and
skips anything already done, so all commands are safe to re-run.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) and [ffmpeg](https://ffmpeg.org/) (both on PATH).
2. `uv sync`
3. `copy config.example.toml config.toml` and edit:
   - `library_root` — where processed track days live
   - `mychron.rs3_data_dirs` — Race Studio 3's data folder
   - timezones if your camera/logger clocks aren't on system local time
4. In **Race Studio 3** → Preferences → Data Download: enable automatic
   CSV export on download (redundant parse path; the primary parser reads
   `.xrk` directly via [libxrk](https://pypi.org/project/libxrk/)).
5. For YouTube uploads: create a project in Google Cloud Console, enable the
   *YouTube Data API v3*, create a **Desktop** OAuth client, save the JSON as
   `client_secret.json` in the repo, **publish** the OAuth consent screen
   (otherwise the token expires weekly), then run `uv run mt publish --dry-run`
   once and complete the browser authorization.

## Usage

```
uv run mt run                # full chain: ingest -> correlate -> sync -> render
uv run mt run --publish      # ... and upload to YouTube
uv run mt status             # pipeline state of every track day
uv run mt ingest             # individual stages...
uv run mt correlate 2026-07-12
uv run mt sync 2026-07-12
uv run mt render 2026-07-12
uv run mt publish 2026-07-12 --dry-run
```

### Zero-touch mode

```
uv run mt watch              # poll for new camera/telemetry material, run pipeline
uv run mt watch --install    # install as a Windows Scheduled Task (at logon)
```

With the watcher running the only human actions per track day are physical:
plug in the camera (or its SD card) and have the MyChron in WiFi range. If
`[rs3] enabled = true` the watcher also drives Race Studio 3's download UI
periodically (GUI automation — brittle across RS3 updates; everything else
still works if you click Download yourself).

### Sync

Clips are aligned to telemetry by cross-correlating the engine sound
(loudness + dominant firing frequency) against the logged RPM trace. Each
sync gets a confidence score; clips below `render.min_sync_confidence` are
not rendered. Escape hatch:

```
uv run mt sync 2026-07-12 --clip DJI_0042.MP4 --video-start "2026-07-12T13:05:02.30+00:00"
```

Solved clips seed the rest of the day (camera clock drift is stable within a
track day).

## Notes & limitations

- **YouTube uploads land private**: the API locks uploads from projects that
  haven't passed Google's (free) compliance audit. Pass the audit and set
  `youtube.privacy = "public"` if you ever want auto-public uploads.
- **DJI Action 5 Pro has no GPS/API** — hence audio sync and SD-card ingestion.
- **Race Studio 3 has no CLI** — hence the optional GUI automation. If AiM
  redesigns the UI, adjust `[rs3] download_button_names` / `window_title_re`.
- The `.xrk` parser (`libxrk`) reads GPS, RPM, temperatures and lap markers;
  the session's absolute start time comes from the file's `Log Date`/`Log
  Time` metadata interpreted in `mychron.timezone`.

## Development

```
uv run pytest
```

Tests cover each stage including an end-to-end render against a generated
test clip (requires ffmpeg). Sync correlation is tested against synthesized
engine audio with a known offset.
