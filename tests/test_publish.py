from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from media_tools.library import (
    DayManifest,
    Lap,
    RenderOutput,
    SyncInfo,
    TelemetryLog,
    TrackSession,
    VideoClip,
)
from media_tools.publish import publish_day


def make_manifest(day_dir: Path) -> DayManifest:
    start = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    (day_dir / "out").mkdir(parents=True, exist_ok=True)
    (day_dir / "out" / "clip_overlay.mp4").write_bytes(b"video")
    return DayManifest(
        date=date(2026, 7, 12),
        track="Interlagos",
        videos=[
            VideoClip(
                file="raw/video/clip.MP4", source_name="clip.MP4", size_bytes=1,
                start_utc_estimate=start,
                sync=SyncInfo(video_start_utc=start, confidence=0.9, method="audio-rpm"),
            )
        ],
        sessions=[TrackSession(id=1, start_utc=start, end_utc=start + timedelta(minutes=15))],
        telemetry=[
            TelemetryLog(
                file="raw/telemetry/s.xrk", source_name="s.xrk", size_bytes=1,
                start_utc=start, end_utc=start + timedelta(minutes=15), session_id=1,
                # out-lap, one complete 61.555 s lap (best), in-lap - so the
                # middle lap is beacon opened+closed (a real complete lap).
                laps=[
                    Lap(num=1, start_s=0.0, end_s=62.345),
                    Lap(num=2, start_s=62.345, end_s=123.9),
                    Lap(num=3, start_s=123.9, end_s=185.0),
                ],
            )
        ],
        renders=[
            RenderOutput(
                file="out/clip_overlay.mp4", session_id=1, kind="session",
                rendered_at=start, source_videos=["raw/video/clip.MP4"],
            )
        ],
    )


def test_publish_day_uploads_and_records(cfg, tmp_path):
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    calls = []

    def fake_uploader(path, title, description, privacy, playlist_id):
        calls.append((path.name, title, privacy, playlist_id))
        return "abc123"

    report = publish_day(cfg, manifest, day_dir, uploader=fake_uploader)
    assert calls == [
        (
            "clip_overlay.mp4",
            "Karting Interlagos 2026-07-12 - session 1 (best lap 1:01.56)",
            "private",
            None,
        )
    ]
    assert manifest.publishes[0].video_id == "abc123"
    assert manifest.publishes[0].url == "https://youtu.be/abc123"
    assert report[0].startswith("+")

    # already published -> nothing to do, uploader not called again
    report2 = publish_day(cfg, manifest, day_dir, uploader=fake_uploader)
    assert report2 == ["nothing to publish"]
    assert len(calls) == 1


def test_title_best_lap_ignores_fragments_and_impossible_laps(cfg, tmp_path):
    from media_tools.publish import _title_context

    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    # out-lap, the real 61.555 s lap, a 17 s fragment, a 39 s cut-track lap,
    # then the in-lap - all beacon-complete except out/in, so only duration
    # rules (0.6x median + min_lap_s) can drop the fragment and cut-track lap.
    manifest.telemetry[0].laps = [
        Lap(num=1, start_s=0.0, end_s=62.345),
        Lap(num=2, start_s=62.345, end_s=123.9),
        Lap(num=3, start_s=123.9, end_s=140.9),
        Lap(num=4, start_s=140.9, end_s=179.9),
        Lap(num=5, start_s=179.9, end_s=240.0),
    ]
    ctx = _title_context(day_dir, manifest, 1, min_lap_s=42.0)
    # Best is the 61.555 s real lap, not 17 s or 39 s.
    assert ctx.best_lap == "1:01.56"


def test_publish_slice_gets_labeled_title(cfg, tmp_path):
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    (day_dir / "out" / "slices").mkdir(parents=True)
    (day_dir / "out" / "slices" / "clip_overlay_25m15s-30m37s.mp4").write_bytes(b"v")
    manifest.publishes = []
    manifest.renders = [
        RenderOutput(
            file="out/slices/clip_overlay_25m15s-30m37s.mp4",
            session_id=1,
            kind="slice",
            label="25:15-30:37",
            rendered_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
            source_videos=["raw/video/clip.MP4"],
        )
    ]
    calls = []
    publish_day(cfg, manifest, day_dir, uploader=lambda p, t, d, pr, pl: calls.append(t) or "x")
    assert calls == ["Karting Interlagos 2026-07-12 - session 1 (best lap 1:01.56) - 25:15-30:37"]


def test_publish_force_reuploads_already_published(cfg, tmp_path):
    """--force re-uploads a render already on YouTube (e.g. a re-rendered
    clip): a second normal publish is a no-op, but force uploads again and
    appends a new publish record beside the old one."""
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    calls = []

    def up(path, title, description, privacy, playlist_id):
        calls.append(path.name)
        return f"vid{len(calls)}"

    publish_day(cfg, manifest, day_dir, uploader=up)
    assert len(calls) == 1 and len(manifest.publishes) == 1

    # without force: already published -> skipped
    assert publish_day(cfg, manifest, day_dir, uploader=up) == ["nothing to publish"]
    assert len(calls) == 1

    # with force: uploads again, keeping the prior record and adding a new one
    report = publish_day(cfg, manifest, day_dir, uploader=up, force=True)
    assert len(calls) == 2
    assert len(manifest.publishes) == 2
    assert manifest.publishes[-1].video_id == "vid2"
    assert any(line.startswith("+") for line in report)


def test_publish_uses_item_title_and_appends_best_lap(cfg, tmp_path):
    """A review-item title is used verbatim; the best lap is appended only
    when the item asks for it."""
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    manifest.renders[0].title = "Custom Title"
    manifest.renders[0].append_best_lap = True
    calls = []
    publish_day(cfg, manifest, day_dir, uploader=lambda p, t, d, pr, pl: calls.append(t) or "x")
    assert calls == ["Custom Title - 1:01.56"]


def test_publish_item_title_without_best_lap(cfg, tmp_path):
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    manifest.renders[0].title = "Custom Title"
    manifest.renders[0].append_best_lap = False
    calls = []
    publish_day(cfg, manifest, day_dir, uploader=lambda p, t, d, pr, pl: calls.append(t) or "x")
    assert calls == ["Custom Title"]


def test_publish_dry_run_uploads_nothing(cfg, tmp_path):
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    report = publish_day(cfg, manifest, day_dir, dry_run=True)
    assert report[0].startswith("~ would upload")
    assert manifest.publishes == []


def test_publish_refuses_raw_clip_paths(cfg, tmp_path):
    """Nothing outside out/ (i.e. no raw, un-overlaid video) may ever upload."""
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    manifest.renders[0].file = "raw/video/clip.MP4"
    (day_dir / "raw" / "video").mkdir(parents=True)
    (day_dir / "raw" / "video" / "clip.MP4").write_bytes(b"raw")

    calls = []
    report = publish_day(cfg, manifest, day_dir, uploader=lambda *a: calls.append(a) or "x")
    assert calls == [] and manifest.publishes == []
    assert "refusing to upload" in report[0]


def test_publish_refuses_render_of_unsynced_clip(cfg, tmp_path):
    """A render whose source clip has no telemetry sync must not upload."""
    from media_tools.library import VideoClip

    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    # Source clip exists in the manifest but carries no sync.
    manifest.videos = [
        VideoClip(
            file="raw/video/clip.MP4", source_name="clip.MP4", size_bytes=1,
            start_utc_estimate=manifest.sessions[0].start_utc,
        )
    ]
    calls = []
    report = publish_day(cfg, manifest, day_dir, uploader=lambda *a: calls.append(a) or "x")
    assert calls == [] and manifest.publishes == []
    assert "no telemetry sync" in report[0]


def test_publish_missing_file_is_reported(cfg, tmp_path):
    day_dir = tmp_path / "day"
    manifest = make_manifest(day_dir)
    (day_dir / "out" / "clip_overlay.mp4").unlink()
    report = publish_day(cfg, manifest, day_dir, uploader=lambda *a: "x")
    assert report[0].startswith("!") and manifest.publishes == []
