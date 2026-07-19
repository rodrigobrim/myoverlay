"""YouTube publish stage.

Uploads rendered videos via the YouTube Data API. One-time setup: create an
OAuth client (Desktop app) in Google Cloud Console, enable the YouTube Data
API v3, save the client secret JSON at youtube.client_secret_file, publish
the OAuth consent screen (otherwise refresh tokens expire after 7 days), and
run `mt publish` once interactively to authorize. After that the persisted
refresh token keeps uploads fully unattended.

Note: uploads from API projects that never passed Google's compliance audit
are locked to private regardless of the requested privacy status.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .i18n import strings as i18n_strings
from .library import DayManifest, PublishRecord, utcnow
from .overlay import fmt_laptime
from .telemetry import complete_laps, session_laps

SCOPES = ["https://www.googleapis.com/auth/youtube"]

# uploader(path, title, description, privacy, playlist_id) -> video_id
Uploader = Callable[[Path, str, str, str, str | None], str]


@dataclass
class TitleContext:
    track: str
    date: str
    session: int
    best_lap: str


def _title_context(
    manifest: DayManifest, session_id: int | None, min_lap_s: float = 0.0
) -> TitleContext:
    durations = []
    for session in manifest.sessions:
        if session_id is None or session.id == session_id:
            laps = complete_laps(session_laps(manifest, session))
            durations += [e - st for _, st, e in laps]
    # Same validity rules as the overlay: only complete (beacon opened+closed)
    # laps, and among those drop fragments / sub-minimum laps (cut track) so
    # neither the out-lap nor an in-lap fragment becomes the title's best lap.
    if durations:
        median = sorted(durations)[len(durations) // 2]
        durations = [
            d for d in durations if d >= 0.6 * median and (min_lap_s <= 0 or d >= min_lap_s)
        ]
    return TitleContext(
        track=manifest.track or "karting",
        date=manifest.date.isoformat(),
        session=session_id if session_id is not None else 0,
        best_lap=fmt_laptime(min(durations) if durations else None),
    )


def get_credentials(cfg: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    token_file = cfg.youtube.token_file
    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not cfg.youtube.client_secret_file.is_file():
            raise FileNotFoundError(
                f"YouTube OAuth client secret not found at {cfg.youtube.client_secret_file}. "
                "Create a Desktop OAuth client in Google Cloud Console (YouTube Data API v3)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(cfg.youtube.client_secret_file), SCOPES
        )
        creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def api_uploader(cfg: Config) -> Uploader:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=get_credentials(cfg))

    def upload(path: Path, title: str, description: str, privacy: str, playlist_id: str | None) -> str:
        media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title[:100], "description": description[:4900]},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            },
            media_body=media,
        )
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response["id"]
        if playlist_id:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
        return video_id

    return upload


def publish_day(
    cfg: Config,
    manifest: DayManifest,
    day_dir: Path,
    uploader: Uploader | None = None,
    dry_run: bool = False,
    clip_filter: str | None = None,
    save: Callable[[], None] | None = None,
) -> list[str]:
    report: list[str] = []
    published_files = {p.file for p in manifest.publishes}
    pending = [r for r in manifest.renders if r.file not in published_files]
    if clip_filter:
        pending = [r for r in pending if clip_filter.lower() in r.file.lower()]
    if not pending:
        return ["nothing to publish"]
    if uploader is None and not dry_run:
        uploader = api_uploader(cfg)

    # Invariant: only overlay renders are ever uploaded - never raw clips.
    # publish iterates manifest.renders exclusively (all created by the render
    # stage), and each render's source clip must still hold a valid sync.
    synced_clips = {v.file for v in manifest.videos if v.sync is not None}

    for render in pending:
        if not render.file.startswith("out/"):
            report.append(f"! {render.file}: not a pipeline render output, refusing to upload")
            continue
        if any(src not in synced_clips for src in render.source_videos):
            report.append(
                f"! {render.file}: source clip has no telemetry sync, refusing to upload"
            )
            continue
        ctx = _title_context(manifest, render.session_id, cfg.render.min_lap_s)
        values = {
            "track": ctx.track,
            "date": ctx.date,
            "session": ctx.session,
            "best_lap": ctx.best_lap,
            "lap": render.lap_num if render.lap_num is not None else "",
        }
        # Explicit templates in config.toml win; otherwise the defaults for
        # the configured output language apply.
        t = i18n_strings(cfg.language)
        title_template = cfg.youtube.title_template or t["title_template"]
        description_template = cfg.youtube.description_template or t["description_template"]
        title = title_template.format(**values)
        description = description_template.format(**values)
        if render.lap_num is not None:
            title = f"{title} - {t['lap_word']} {render.lap_num}"
        if render.label:
            title = f"{title} - {render.label}"

        path = day_dir / render.file
        if not path.is_file():
            report.append(f"! {render.file}: rendered file missing, skipped")
            continue
        if dry_run:
            report.append(f"~ would upload {render.file} as '{title}' ({cfg.youtube.privacy})")
            continue

        video_id = uploader(path, title, description, cfg.youtube.privacy, cfg.youtube.playlist_id)
        manifest.publishes.append(
            PublishRecord(
                file=render.file,
                video_id=video_id,
                url=f"https://youtu.be/{video_id}",
                privacy=cfg.youtube.privacy,
                published_at=utcnow(),
            )
        )
        # Persist immediately: a later upload failing in this batch must never
        # orphan a video that already went up (the record survives the crash).
        if save is not None:
            save()
        report.append(f"+ {render.file} -> https://youtu.be/{video_id} ({cfg.youtube.privacy})")
    return report
