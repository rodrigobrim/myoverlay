"""Media library layout and per-track-day manifest.

Layout under `library_root`:

    2026-07-12/
        session.json     <- DayManifest, the pipeline's source of truth
        raw/video/       <- ingested camera clips (original filenames)
        raw/telemetry/   <- ingested .xrk files
        work/            <- intermediate artifacts (unified telemetry, GPX/FIT)
        out/             <- rendered videos ready to publish

Every pipeline stage reads and updates the manifest, and skips work already
recorded there, so all stages are idempotent and re-runnable.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

MANIFEST_NAME = "session.json"
DAY_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class SyncInfo(BaseModel):
    """Video-to-telemetry alignment for one clip.

    offset_s: seconds to add to a clip-relative time to get UTC
    (i.e. the UTC timestamp of the first video frame, as epoch seconds).
    """

    video_start_utc: datetime
    confidence: float
    method: str  # "audio-rpm" | "manual" | "seeded"


class RaceEnd(BaseModel):
    """Result of scanning the clip's audio for the end-of-race engine
    shutdown (scan-video-for-race-end). Present = scanned; cut_at_s None
    means the engine was still running when the recording stopped (no trim).
    Times are video seconds from clip start."""

    engine_stop_s: float | None = None
    cut_at_s: float | None = None


class VideoClip(BaseModel):
    file: str  # path relative to the day dir, e.g. raw/video/DJI_...MP4
    source_name: str
    size_bytes: int
    duration_s: float | None = None
    # Best-effort capture start (from filename or mtime) before real sync.
    start_utc_estimate: datetime
    sync: SyncInfo | None = None
    session_id: int | None = None
    # None = not scanned yet (see RaceEnd).
    race_end: RaceEnd | None = None
    # When this clip is a join of camera-split segments (GoPro/DJI roll a long
    # recording into ~4 GB files), the ordered segment paths it was built from,
    # relative to the day dir. Empty for an ordinary single-file clip.
    segments: list[str] = Field(default_factory=list)


class Lap(BaseModel):
    num: int
    start_s: float  # relative to telemetry log start
    end_s: float


class TelemetryLog(BaseModel):
    file: str
    source_name: str
    size_bytes: int
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    venue: str | None = None
    driver: str | None = None
    laps: list[Lap] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    session_id: int | None = None


class TrackSession(BaseModel):
    """One on-track stint: a telemetry window plus the clips that cover it."""

    id: int
    start_utc: datetime
    end_utc: datetime
    telemetry_files: list[str] = Field(default_factory=list)
    video_files: list[str] = Field(default_factory=list)


class RenderOutput(BaseModel):
    file: str  # relative to day dir, under out/
    session_id: int | None = None
    kind: str  # "session" | "lap" | "slice"
    lap_num: int | None = None
    # Free-text qualifier appended to the YouTube title (e.g. "25:15-30:37").
    label: str | None = None
    rendered_at: datetime
    source_videos: list[str] = Field(default_factory=list)
    # Per-item overrides chosen in the review GUI. When title is set, publish
    # uses it verbatim as the base title instead of the template; description
    # likewise overrides the templated body; append_best_lap adds the best lap
    # to the title. None/default preserves the legacy templated behaviour.
    title: str | None = None
    description: str | None = None
    append_best_lap: bool = True


class PublishRecord(BaseModel):
    file: str
    video_id: str
    url: str
    privacy: str
    published_at: datetime


class ConsumedSegment(BaseModel):
    """A camera-split segment that has been joined into a single clip and so is
    no longer an active video in its own right. Kept only so re-ingesting the
    same card does not re-add it (its raw file stays on disk, never deleted)."""

    source_name: str
    size_bytes: int


class DayManifest(BaseModel):
    date: date
    track: str | None = None
    videos: list[VideoClip] = Field(default_factory=list)
    telemetry: list[TelemetryLog] = Field(default_factory=list)
    sessions: list[TrackSession] = Field(default_factory=list)
    renders: list[RenderOutput] = Field(default_factory=list)
    publishes: list[PublishRecord] = Field(default_factory=list)
    # Split segments already joined into a single clip (see ConsumedSegment).
    consumed_segments: list[ConsumedSegment] = Field(default_factory=list)

    def has_video(self, source_name: str, size_bytes: int) -> bool:
        return any(
            v.source_name == source_name and v.size_bytes == size_bytes for v in self.videos
        ) or any(
            c.source_name == source_name and c.size_bytes == size_bytes
            for c in self.consumed_segments
        )

    def has_telemetry(self, source_name: str, size_bytes: int) -> bool:
        return any(
            t.source_name == source_name and t.size_bytes == size_bytes for t in self.telemetry
        )


class Library:
    def __init__(self, root: Path):
        self.root = root

    def day_dir(self, d: date) -> Path:
        return self.root / d.isoformat()

    def ensure_day(self, d: date) -> Path:
        day = self.day_dir(d)
        for sub in ("raw/video", "raw/telemetry", "work", "out"):
            (day / sub).mkdir(parents=True, exist_ok=True)
        return day

    def day_dates(self) -> list[date]:
        if not self.root.is_dir():
            return []
        out = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir() and DAY_DIR_RE.match(child.name):
                out.append(date.fromisoformat(child.name))
        return out

    def load_day(self, d: date) -> DayManifest:
        path = self.day_dir(d) / MANIFEST_NAME
        if path.is_file():
            # utf-8-sig: tolerate a BOM if the manifest was hand-edited on
            # Windows (e.g. to pin a sync offset); we always write without one.
            return DayManifest.model_validate_json(path.read_bytes().decode("utf-8-sig"))
        return DayManifest(date=d)

    def save_day(self, manifest: DayManifest) -> Path:
        day = self.ensure_day(manifest.date)
        path = day / MANIFEST_NAME
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)
        return path

    def known_videos(self) -> set[tuple[str, int]]:
        """(source_name, size) of every clip already ingested, across all days.

        Includes segments already joined into a clip (consumed_segments), so a
        re-ingest of the same card does not resurrect them as separate clips.
        """
        seen: set[tuple[str, int]] = set()
        for d in self.day_dates():
            m = self.load_day(d)
            for v in m.videos:
                seen.add((v.source_name, v.size_bytes))
            for c in m.consumed_segments:
                seen.add((c.source_name, c.size_bytes))
        return seen

    def known_telemetry(self) -> set[tuple[str, int]]:
        seen: set[tuple[str, int]] = set()
        for d in self.day_dates():
            for t in self.load_day(d).telemetry:
                seen.add((t.source_name, t.size_bytes))
        return seen


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
