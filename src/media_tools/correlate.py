"""Correlate ingested files into track sessions.

Telemetry logs are authoritative (GPS-timestamped): each group of
overlapping logs becomes one TrackSession. Video clips are assigned to the
session their estimated time range overlaps, allowing for camera clock drift
via the configured tolerance. Once a clip is properly synced (M3), its
refined start time re-runs through here and tightens the assignment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .library import DayManifest, TrackSession


@dataclass
class CorrelateReport:
    sessions: int = 0
    assigned_videos: int = 0
    unassigned_videos: list[str] = field(default_factory=list)
    ambiguous_videos: list[str] = field(default_factory=list)


def _overlap_s(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return (earliest_end - latest_start).total_seconds()


def correlate_day(manifest: DayManifest, clock_tolerance_s: float = 900.0) -> CorrelateReport:
    report = CorrelateReport()

    timed = [t for t in manifest.telemetry if t.start_utc and t.end_utc]
    timed.sort(key=lambda t: t.start_utc)

    # Merge overlapping telemetry logs into sessions.
    sessions: list[TrackSession] = []
    for log in timed:
        if sessions and log.start_utc <= sessions[-1].end_utc:
            cur = sessions[-1]
            cur.end_utc = max(cur.end_utc, log.end_utc)
            cur.telemetry_files.append(log.file)
        else:
            sessions.append(
                TrackSession(
                    id=len(sessions) + 1,
                    start_utc=log.start_utc,
                    end_utc=log.end_utc,
                    telemetry_files=[log.file],
                )
            )
        log.session_id = sessions[-1].id

    # Assign clips to the session with the largest overlap (within tolerance).
    tolerance = timedelta(seconds=clock_tolerance_s)
    for clip in manifest.videos:
        clip_start = clip.start_utc_estimate
        clip_end = clip_start + timedelta(seconds=clip.duration_s or 0.0)
        # A synced clip has an exact start; use it and drop the tolerance.
        slack = timedelta(0) if clip.sync else tolerance
        if clip.sync:
            clip_start = clip.sync.video_start_utc
            clip_end = clip_start + timedelta(seconds=clip.duration_s or 0.0)

        candidates = []
        for session in sessions:
            ov = _overlap_s(
                clip_start - slack, clip_end + slack, session.start_utc, session.end_utc
            )
            if ov > 0:
                candidates.append((ov, session))

        candidates.sort(key=lambda c: -c[0])
        if not candidates:
            clip.session_id = None
            report.unassigned_videos.append(clip.file)
            continue
        if len(candidates) > 1 and candidates[1][0] >= candidates[0][0] * 0.5:
            # Two sessions claim comparable overlap: flag it rather than guess
            # silently, but still take the best candidate.
            report.ambiguous_videos.append(clip.file)

        best = candidates[0][1]
        clip.session_id = best.id
        if clip.file not in best.video_files:
            best.video_files.append(clip.file)
        report.assigned_videos += 1

    manifest.sessions = sessions
    report.sessions = len(sessions)
    return report
