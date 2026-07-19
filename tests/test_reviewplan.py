"""Render-plan model (Gate 2): defaults from the manifest, plan-file
round-trip, and execute_item driving join + render_clip."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from media_tools.library import (
    DayManifest, Lap, SyncInfo, TelemetryLog, TrackSession, VideoClip,
)
from media_tools.reviewplan import (
    RenderSlice, build_plan, execute_item, load_plan, plan_path, save_plan,
)

UTC = timezone.utc
START = datetime(2026, 7, 16, 22, 14, tzinfo=UTC)


def _manifest(day_dir, clip=None):
    (day_dir / "raw" / "video").mkdir(parents=True, exist_ok=True)
    (day_dir / "raw" / "telemetry").mkdir(parents=True, exist_ok=True)
    laps = [Lap(num=0, start_s=0, end_s=10), Lap(num=1, start_s=10, end_s=70),
            Lap(num=2, start_s=70, end_s=129), Lap(num=3, start_s=129, end_s=145)]
    clip = clip or VideoClip(
        file="raw/video/c.MP4", source_name="c.MP4", size_bytes=1, duration_s=200.0,
        start_utc_estimate=START, session_id=1,
        sync=SyncInfo(video_start_utc=START, confidence=0.9, method="audio-rpm"),
    )
    return DayManifest(
        date=date(2026, 7, 16), track="kgv", videos=[clip],
        sessions=[TrackSession(id=1, start_utc=START, end_utc=START + timedelta(minutes=15),
                               telemetry_files=["raw/telemetry/s.xrk"], video_files=[clip.file])],
        telemetry=[TelemetryLog(file="raw/telemetry/s.xrk", source_name="s.xrk", size_bytes=1,
                                start_utc=START, end_utc=START + timedelta(minutes=15),
                                session_id=1, laps=laps)],
    )


def test_build_plan_defaults(cfg, tmp_path):
    day_dir = tmp_path / "day"
    plan = build_plan(cfg, day_dir, _manifest(day_dir))
    assert len(plan.items) == 1
    it = plan.items[0]
    assert it.quality == cfg.render.resolution  # "2k"
    assert it.slices == [RenderSlice(file="raw/video/c.MP4", source_name="c.MP4")]
    assert it.telemetry_files == ["raw/telemetry/s.xrk"]
    assert it.best_lap == "0:59.00"  # lap 70->129 = 59 s, via the SSOT
    assert it.append_best_lap is True
    assert it.title and "best lap" not in it.title.lower()  # not baked into base title


def test_unsynced_clip_is_skipped(cfg, tmp_path):
    day_dir = tmp_path / "day"
    clip = VideoClip(file="raw/video/c.MP4", source_name="c.MP4", size_bytes=1,
                     duration_s=200.0, start_utc_estimate=START, session_id=1)  # no sync
    assert build_plan(cfg, day_dir, _manifest(day_dir, clip)).items == []


def test_plan_round_trip(cfg, tmp_path):
    day_dir = tmp_path / "day"
    plan = build_plan(cfg, day_dir, _manifest(day_dir))
    save_plan(day_dir, plan)
    assert load_plan(plan_path(day_dir)) == plan


def test_execute_item_drives_render_clip(cfg, tmp_path, monkeypatch):
    day_dir = tmp_path / "day"
    manifest = _manifest(day_dir)
    plan = build_plan(cfg, day_dir, manifest)
    it = plan.items[0]
    it.start_enabled, it.start_s = True, 5.0
    it.quality, it.title, it.append_best_lap = "hd", "My Title", False

    captured = {}

    def fake_render_clip(cfg, day_dir, manifest, clip, day, **kw):
        captured.update(kw)
        captured["clip"] = clip.file
        return Path("out/c_overlay.mp4")

    monkeypatch.setattr("media_tools.render.render_clip", fake_render_clip)
    line = execute_item(cfg, manifest, day_dir, None, it)
    assert line.startswith("+")
    assert captured["clip"] == "raw/video/c.MP4"
    assert captured["window_start_s"] == 5.0 and captured["window_end_s"] == 0.0
    assert captured["title"] == "My Title" and captured["append_best_lap"] is False
    assert cfg.render.resolution == "hd"


def test_execute_item_multislice_joins(cfg, tmp_path, monkeypatch):
    day_dir = tmp_path / "day"
    a = VideoClip(file="raw/video/a.MP4", source_name="a.MP4", size_bytes=1, duration_s=100.0,
                  start_utc_estimate=START, session_id=1,
                  sync=SyncInfo(video_start_utc=START, confidence=0.9, method="audio-rpm"))
    manifest = _manifest(day_dir, a)
    manifest.videos.append(VideoClip(
        file="raw/video/b.MP4", source_name="b.MP4", size_bytes=1, duration_s=100.0,
        start_utc_estimate=START + timedelta(seconds=100), session_id=1,
        sync=SyncInfo(video_start_utc=START + timedelta(seconds=100), confidence=0.9, method="audio-rpm")))

    it = build_plan(cfg, day_dir, manifest).items[0]
    it.slices = [RenderSlice(file="raw/video/a.MP4", source_name="a.MP4"),
                 RenderSlice(file="raw/video/b.MP4", source_name="b.MP4")]

    def fake_join_day(manifest, day_dir, only_substrings=None, **kw):
        manifest.videos.append(VideoClip(
            file="raw/video/a-b.MP4", source_name="a-b.MP4", size_bytes=1, duration_s=200.0,
            start_utc_estimate=START, session_id=1, segments=["raw/video/a.MP4", "raw/video/b.MP4"],
            sync=SyncInfo(video_start_utc=START, confidence=0.9, method="audio-rpm")))
        return ["joined a+b"]

    monkeypatch.setattr("media_tools.videojoin.join_day", fake_join_day)
    monkeypatch.setattr("media_tools.render.render_clip", lambda *a, **k: Path("out/j.mp4"))
    line = execute_item(cfg, manifest, day_dir, None, it)
    assert line.startswith("+")
    assert any(c.segments == ["raw/video/a.MP4", "raw/video/b.MP4"] for c in manifest.videos)
