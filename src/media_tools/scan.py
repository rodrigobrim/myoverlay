"""Read-only preview of new, not-yet-ingested content (review Gate 1).

Enumerates new camera clips + MyChron telemetry (identity = name+size, exactly
as ingest does), correlates telemetry under the video it time-overlaps, and
flags orphan telemetry - telemetry with no matching video, which is still
committed on ingest, it just yields no render item. Writes nothing.

Reuses the ingest scanners (via their modules, so the test no_real_volumes
guard still applies) and the correlate overlap primitive, so the preview
matches what `mt ingest` + `mt correlate` will actually do.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from .config import Config
from .correlate import _overlap_s
from .ingest import camera as _cam
from .ingest import mychron as _my
from .library import Library
from .overlay import fmt_laptime
from .telemetry import best_lap


class ScanTelemetry(BaseModel):
    source_name: str
    size_bytes: int
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    venue: str | None = None
    driver: str | None = None
    lap_count: int = 0
    best_lap: str = "-:--.--"


class ScanVideo(BaseModel):
    source_name: str
    size_bytes: int
    start_utc: datetime
    end_utc: datetime | None = None
    duration_s: float | None = None


class ScanVideoGroup(BaseModel):
    video: ScanVideo
    telemetry: list[ScanTelemetry] = []


class ScanResult(BaseModel):
    date_guess: str | None = None
    video_groups: list[ScanVideoGroup] = []
    # committed on ingest, but no video -> no render item:
    orphan_telemetry: list[ScanTelemetry] = []


def _telemetry_window(cfg: Config, path, logger_tz):
    """(XrkInfo, start_utc, end_utc), applying the same wrong-clock correction
    and mtime fallback as ingest_mychron so the preview times line up."""
    info = _my.parse_xrk(path, logger_tz)
    start = info.start_utc
    if start is not None and start > datetime.now(timezone.utc) + timedelta(days=90):
        start = start + cfg.mychron.clock_offset()
    if start is None:
        start = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) - timedelta(
            seconds=info.duration_s
        )
    return info, start, start + timedelta(seconds=info.duration_s)


def scan_new(cfg: Config) -> ScanResult:
    lib = Library(cfg.library_root)

    # --- new telemetry (name+size not already ingested) ---
    logger_tz = cfg.mychron.tzinfo()
    known_tel = lib.known_telemetry()
    tel_entries: list[tuple[ScanTelemetry, datetime, datetime]] = []
    for path in _my.scan_sources(cfg):
        size = path.stat().st_size
        if (path.name, size) in known_tel:
            continue
        try:
            info, start, end = _telemetry_window(cfg, path, logger_tz)
        except Exception:  # noqa: BLE001 - a corrupt .xrk must not abort the scan
            continue
        lap = best_lap([(lp.num, lp.start_s, lp.end_s) for lp in info.laps], cfg.render.min_lap_s)
        tel_entries.append((
            ScanTelemetry(
                source_name=path.name, size_bytes=size, start_utc=start, end_utc=end,
                venue=info.venue, driver=info.driver, lap_count=len(info.laps),
                best_lap=fmt_laptime((lap[2] - lap[1]) if lap else None),
            ),
            start, end,
        ))

    # --- new camera clips (name+size not already ingested) ---
    camera_tz = cfg.camera.tzinfo()
    sources = _cam.find_dcim_sources() + list(cfg.camera.source_dirs)
    known_vid = lib.known_videos()
    vid_entries: list[tuple[ScanVideo, datetime, datetime | None]] = []
    for path in _cam.iter_source_videos(sources, cfg.camera.extensions):
        size = path.stat().st_size
        if (path.name, size) in known_vid:
            continue
        start = _cam.capture_time(path, camera_tz)
        dur = _cam.probe_duration_s(path)
        end = start + timedelta(seconds=dur) if dur else None
        vid_entries.append((
            ScanVideo(source_name=path.name, size_bytes=size, start_utc=start, end_utc=end, duration_s=dur),
            start, end,
        ))

    # --- correlate: attach each telemetry to the best-overlapping video ---
    # (telemetry is authoritative; a video keeps camera-clock slack, matching
    # correlate.py). Telemetry that overlaps no video is an orphan.
    tol = timedelta(seconds=cfg.camera.clock_tolerance_s)
    groups = [ScanVideoGroup(video=v) for v, _, _ in vid_entries]
    orphans: list[ScanTelemetry] = []
    for st, ts, te in tel_entries:
        best_i, best_ov = None, 0.0
        for i, (_v, vs, ve) in enumerate(vid_entries):
            ov = _overlap_s(vs - tol, (ve or vs) + tol, ts, te)
            if ov > best_ov:
                best_i, best_ov = i, ov
        (orphans if best_i is None else groups[best_i].telemetry).append(st)

    starts = [ts for _, ts, _ in tel_entries] + [vs for _, vs, _ in vid_entries]
    date_guess = min(starts).astimezone(logger_tz).date().isoformat() if starts else None
    return ScanResult(date_guess=date_guess, video_groups=groups, orphan_telemetry=orphans)
