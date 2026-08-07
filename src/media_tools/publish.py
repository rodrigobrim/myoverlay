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

from pathlib import Path
from typing import Callable

from .config import Config
from .i18n import strings as i18n_strings
from .library import DayManifest, PublishRecord, utcnow
from .meta import (
    DetailOverrides,
    Updater,
    compose_description,
    compose_title,
    composed_details,
    render_session_id,
    title_context,
)

SCOPES = ["https://www.googleapis.com/auth/youtube"]

# uploader(path, title, description, privacy, playlist_id) -> video_id
Uploader = Callable[[Path, str, str, str, str | None], str]


def _token_client_mismatch(cfg: Config, creds) -> bool:
    """True when the saved token was issued by a different OAuth client than
    the one in client_secret.json. Unreadable/odd files answer False: this is a
    guard against a silent stale-token failure, never a new way to fail."""
    import json

    token_client = getattr(creds, "client_id", None)
    if not token_client:
        return False
    try:
        secret = json.loads(
            cfg.youtube.client_secret_file.read_text(encoding="utf-8-sig")
        )["installed"]["client_id"]
    except Exception:  # noqa: BLE001 - missing/unparsable secret: not our call
        return False
    return token_client != secret


def get_credentials(cfg: Config):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    token_file = cfg.youtube.token_file
    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    # A token is bound to the OAuth client that issued it. Re-running
    # google-setup mints a NEW client (the old secret is unrecoverable, and a
    # deleted project forces a fresh one), and the stale token still parses and
    # still looks unexpired - so without this check it is accepted here and
    # only fails later, at the first API call, as an opaque auth error.
    if creds and _token_client_mismatch(cfg, creds):
        creds = None
    if creds and creds.expired and creds.refresh_token:
        from google.auth.exceptions import RefreshError

        try:
            creds.refresh(Request())
        except RefreshError as exc:
            # deleted_client/invalid_client: the OAuth client that issued this
            # token no longer exists in Google Cloud (client or whole project
            # deleted). Re-consent cannot fix that - only a new client can, so
            # say so instead of dying on a raw traceback at the first upload.
            if "deleted_client" in str(exc) or "invalid_client" in str(exc):
                raise RuntimeError(
                    "The Google OAuth client behind "
                    f"{cfg.youtube.token_file} no longer exists in Google Cloud "
                    f"(project {cfg.youtube.project_id}). Run `mt google-setup` "
                    "to create a new one, then `mt google-auth`."
                ) from exc
            # Revoked or expired grant: a fresh interactive consent still works.
            creds = None
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


def published_report(manifest: DayManifest, clip_filter: str | None = None) -> list[str]:
    """What is on YouTube for this day, from the manifest's publish registry:
    one block per rendered file, the latest upload (the current video) first,
    older --force re-uploads listed as superseded."""
    lines: list[str] = []
    for file, records in manifest.publishes.items():
        if clip_filter and clip_filter.lower() not in file.lower():
            continue
        rec = records[-1]
        lines.append(
            f"{file} -> {rec.url} ({rec.privacy}, published {rec.published_at:%Y-%m-%d %H:%M} UTC)"
        )
        if rec.title:
            lines.append(f"    title: {rec.title}")
        for old in records[:-1]:
            lines.append(f"    superseded: {old.url} ({old.published_at:%Y-%m-%d %H:%M} UTC)")
    return lines or ["nothing published"]


def publish_day(
    cfg: Config,
    manifest: DayManifest,
    day_dir: Path,
    uploader: Uploader | None = None,
    dry_run: bool = False,
    clip_filter: str | None = None,
    force: bool = False,
    save: Callable[[], None] | None = None,
    overrides: DetailOverrides | None = None,
    updater: Updater | None = None,
) -> list[str]:
    report: list[str] = []
    published_files = set(manifest.publishes)
    # force re-uploads renders already on YouTube (e.g. a re-rendered clip):
    # the prior publish record is kept and a new video is created alongside it.
    pending = [r for r in manifest.renders if force or r.file not in published_files]
    if clip_filter:
        pending = [r for r in pending if clip_filter.lower() in r.file.lower()]
    if not pending:
        return ["nothing to publish"]
    if uploader is None and not dry_run:
        uploader = api_uploader(cfg)
    if overrides is not None and overrides.wanted and updater is None and not dry_run:
        from .meta import api_updater, youtube_service

        updater = api_updater(youtube_service(cfg))

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
        ctx = title_context(
            day_dir, manifest, render_session_id(manifest, render), cfg.render.min_lap_s
        )
        values = {
            "track": ctx.track,
            "date": ctx.date,
            "session": ctx.session,
            "best_lap": ctx.best_lap,
            "lap": render.lap_num if render.lap_num is not None else "",
        }
        # Explicit templates in config.toml win; otherwise the meta composers
        # (shared with `mt meta`) build "<review-GUI text> - <auto meta>" in
        # the configured language. A review-GUI title with append_best_lap
        # unchecked stays verbatim; an empty title falls back to the auto meta
        # alone (YouTube rejects empty titles).
        t = i18n_strings(cfg.language)
        if cfg.youtube.title_template:
            title = cfg.youtube.title_template.format(**values)
        else:
            no_meta = bool(render.title) and not render.append_best_lap
            title = compose_title(render.title or "", ctx, t, no_meta=no_meta)
        if cfg.youtube.description_template:
            description = cfg.youtube.description_template.format(**values)
        else:
            description = compose_description(render.description or "", ctx, t)
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
            if overrides is not None and overrides.wanted:
                new_title, new_desc = composed_details(overrides, ctx, t)
                for label, value in (
                    ("title", new_title),
                    ("description", new_desc),
                    ("visibility", overrides.visibility),
                ):
                    if value is not None:
                        report.append(f"~   then would set {label}: {value}")
            continue

        video_id = uploader(path, title, description, cfg.youtube.privacy, cfg.youtube.playlist_id)
        record = PublishRecord(
            video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            privacy=cfg.youtube.privacy,
            published_at=utcnow(),
            title=title,
            description=description,
        )
        manifest.publishes.setdefault(render.file, []).append(record)
        # Persist immediately: a later upload failing in this batch must never
        # orphan a video that already went up (the record survives the crash).
        if save is not None:
            save()
        report.append(f"+ {render.file} -> https://youtu.be/{video_id} ({cfg.youtube.privacy})")

        # Requested details are applied the moment the upload returns - the
        # same edit `mt meta` performs, on the video just created. No wait:
        # the id the API hands back is immediately addressable.
        if overrides is not None and overrides.wanted:
            new_title, new_desc = composed_details(overrides, ctx, t)
            try:
                updater(video_id, new_title, new_desc, overrides.visibility)
            except Exception as exc:  # noqa: BLE001 - the video is up; say what failed
                report.append(f"!   details not applied: {exc}")
                continue
            if new_title is not None:
                record.title = new_title
            if new_desc is not None:
                record.description = new_desc
            if overrides.visibility is not None:
                record.privacy = overrides.visibility
            if save is not None:
                save()
            changed = [
                name
                for name, value in (
                    ("title", new_title),
                    ("description", new_desc),
                    ("visibility", overrides.visibility),
                )
                if value is not None
            ]
            report.append(f"    details set: {', '.join(changed)}")
            if new_title is not None:
                report.append(f"    title: {new_title}")
    return report
