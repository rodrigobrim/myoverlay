from datetime import date, datetime, timedelta, timezone

from media_tools.correlate import correlate_day
from media_tools.library import DayManifest, SyncInfo, TelemetryLog, VideoClip


def utc(h, m=0, s=0):
    return datetime(2026, 7, 12, h, m, s, tzinfo=timezone.utc)


def make_telemetry(name, start, end):
    return TelemetryLog(
        file=f"raw/telemetry/{name}",
        source_name=name,
        size_bytes=100,
        start_utc=start,
        end_utc=end,
    )


def make_clip(name, start, duration_s=600.0, sync=None):
    return VideoClip(
        file=f"raw/video/{name}",
        source_name=name,
        size_bytes=100,
        duration_s=duration_s,
        start_utc_estimate=start,
        sync=sync,
    )


def test_sessions_from_telemetry_and_clip_assignment():
    m = DayManifest(date=date(2026, 7, 12))
    m.telemetry = [
        make_telemetry("s1.xrk", utc(13, 0), utc(13, 15)),
        make_telemetry("s2.xrk", utc(15, 0), utc(15, 20)),
    ]
    m.videos = [
        make_clip("a.MP4", utc(13, 2)),          # inside session 1
        make_clip("b.MP4", utc(14, 58)),         # starts just before session 2
        make_clip("lunch.MP4", utc(14, 0)),      # paddock clip, near nothing
    ]

    report = correlate_day(m, clock_tolerance_s=300)
    assert report.sessions == 2
    assert [s.id for s in m.sessions] == [1, 2]
    assert m.videos[0].session_id == 1
    assert m.videos[1].session_id == 2
    assert m.videos[2].session_id is None
    assert report.unassigned_videos == ["raw/video/lunch.MP4"]
    assert m.sessions[0].video_files == ["raw/video/a.MP4"]
    assert m.telemetry[0].session_id == 1


def test_overlapping_telemetry_merges_into_one_session():
    m = DayManifest(date=date(2026, 7, 12))
    m.telemetry = [
        make_telemetry("s1.xrk", utc(13, 0), utc(13, 15)),
        make_telemetry("s1b.xrk", utc(13, 10), utc(13, 25)),
    ]
    report = correlate_day(m)
    assert report.sessions == 1
    assert m.sessions[0].start_utc == utc(13, 0)
    assert m.sessions[0].end_utc == utc(13, 25)
    assert len(m.sessions[0].telemetry_files) == 2


def test_synced_clip_uses_exact_time_without_tolerance():
    m = DayManifest(date=date(2026, 7, 12))
    m.telemetry = [make_telemetry("s1.xrk", utc(13, 0), utc(13, 15))]
    sync = SyncInfo(video_start_utc=utc(13, 1), confidence=0.9, method="audio-rpm")
    # Estimate is way off (bad camera clock) but sync time is right.
    m.videos = [make_clip("a.MP4", utc(9, 0), sync=sync)]

    report = correlate_day(m, clock_tolerance_s=60)
    assert m.videos[0].session_id == 1
    assert report.assigned_videos == 1


def test_rerun_is_stable():
    m = DayManifest(date=date(2026, 7, 12))
    m.telemetry = [make_telemetry("s1.xrk", utc(13, 0), utc(13, 15))]
    m.videos = [make_clip("a.MP4", utc(13, 2))]
    correlate_day(m)
    correlate_day(m)
    assert len(m.sessions) == 1
    assert m.sessions[0].video_files == ["raw/video/a.MP4"]
