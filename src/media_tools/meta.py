"""Published-video metadata: compose, show and update YouTube details.

Single source of truth for how a video's title and description are built:
`mt meta` edits an already-published video and the publish stage composes a
fresh upload through the same functions, so both always produce

    title:       <user text> - <track DD/MM/YY - BL m:ss.ss>
    description: <user text>\\n\\n<recorded-at / best-lap / MyOverlay credit>

in the configured output language ("BL" is the localized best-lap
abbreviation, e.g. "MV" for melhor volta in Portuguese).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Config
from .library import DayManifest
from .overlay import fmt_laptime
from .telemetry import best_lap, session_laps_derived

VISIBILITIES = ("private", "unlisted", "public")
NO_LAP = "-:--.--"


@dataclass
class TitleContext:
    track: str
    date: str
    session: int
    best_lap: str


def title_context(
    day_dir: Path, manifest: DayManifest, session_id: int | None, min_lap_s: float = 0.0
) -> TitleContext:
    # Best lap via the single source of truth (telemetry.best_lap) over the
    # S/F-relap-corrected laps (session_laps_derived), so the title's best lap
    # is exactly the one the overlay shows - never the raw early-beacon lap.
    best_s: float | None = None
    for session in manifest.sessions:
        if session_id is None or session.id == session_id:
            lap = best_lap(session_laps_derived(day_dir, manifest, session), min_lap_s)
            if lap is not None:
                dur = lap[2] - lap[1]
                best_s = dur if best_s is None else min(best_s, dur)
    return TitleContext(
        track=manifest.track or "karting",
        date=manifest.date.isoformat(),
        session=session_id if session_id is not None else 0,
        best_lap=fmt_laptime(best_s),
    )


def title_meta(ctx: TitleContext, t: dict[str, str]) -> str:
    """The automatic title block: "KGV 111 30/07/26 - MV 0:53.41"."""
    day = date.fromisoformat(ctx.date).strftime("%d/%m/%y")
    if ctx.best_lap == NO_LAP:
        return f"{ctx.track} {day}"
    return f"{ctx.track} {day} - {t['best_lap_abbrev']} {ctx.best_lap}"


def description_meta(ctx: TitleContext, t: dict[str, str]) -> str:
    """The automatic description block: recorded-at, best lap, MyOverlay credit."""
    return t["description_template"].format(
        track=ctx.track, date=ctx.date, best_lap=ctx.best_lap
    )


def compose_title(
    user_title: str, ctx: TitleContext, t: dict[str, str], no_meta: bool = False
) -> str:
    user_title = user_title.strip()
    if no_meta:
        return user_title
    meta = title_meta(ctx, t)
    return f"{user_title} - {meta}" if user_title else meta


def compose_description(
    user_desc: str, ctx: TitleContext, t: dict[str, str], no_meta: bool = False
) -> str:
    user_desc = user_desc.strip()
    if no_meta:
        return user_desc
    meta = description_meta(ctx, t)
    return f"{user_desc}\n\n{meta}" if user_desc else meta


def youtube_service(cfg: Config):
    from googleapiclient.discovery import build

    from .publish import get_credentials

    return build("youtube", "v3", credentials=get_credentials(cfg))


def fetch_details(video_id: str, service) -> dict[str, str] | None:
    """Current published details, or None when the video is gone from YouTube."""
    resp = service.videos().list(part="snippet,status", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        return None
    snippet, status = items[0]["snippet"], items[0]["status"]
    return {
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "visibility": status.get("privacyStatus", "?"),
        "published_at": snippet.get("publishedAt", "-"),
    }


def update_details(
    video_id: str,
    service,
    title: str | None = None,
    description: str | None = None,
    visibility: str | None = None,
) -> None:
    """Update only the fields given. The API replaces every mutable field of
    the parts sent, so the current snippet/status are fetched and mutated -
    never rebuilt - or an update of just the title would wipe categoryId,
    selfDeclaredMadeForKids, etc."""
    resp = service.videos().list(part="snippet,status", id=video_id).execute()
    items = resp.get("items") or []
    if not items:
        raise ValueError(f"video {video_id} no longer exists on YouTube")
    snippet, status = items[0]["snippet"], items[0]["status"]
    if title is not None:
        snippet["title"] = title[:100]
    if description is not None:
        snippet["description"] = description[:4900]
    if visibility is not None:
        status["privacyStatus"] = visibility
    service.videos().update(
        part="snippet,status",
        body={"id": video_id, "snippet": snippet, "status": status},
    ).execute()
