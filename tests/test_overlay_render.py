import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from media_tools.overlay import FrameValues, OverlayRenderer, fmt_laptime, track_outline_frac
from media_tools.render import render_day, sample_timeline
from media_tools.library import (
    DayManifest,
    Lap,
    SyncInfo,
    TelemetryLog,
    TrackSession,
    VideoClip,
)


def test_fmt_laptime():
    assert fmt_laptime(62.345) == "1:02.34"
    assert fmt_laptime(None) == "-:--.--"


def test_track_projection_nan_passthrough():
    """NaN GPS rows (pit gaps) must not poison the projection transform."""
    from media_tools.overlay import TrackProjection

    lat = np.array([-23.7010, np.nan, -23.7015, -23.7020])
    lon = np.array([-46.6970, np.nan, -46.6980, -46.6975])
    proj = TrackProjection(lat, lon)
    pts = proj.project(lat, lon)
    assert np.isnan(pts[1]).all()
    finite = pts[[0, 2, 3]]
    assert np.isfinite(finite).all()
    assert finite.min() >= 0.0 and finite.max() <= 1.0


def test_output_size():
    from media_tools.render import output_size

    assert output_size(3840, 2160, 1440) == (2560, 1440)
    assert output_size(3840, 2160, 1080) == (1920, 1080)
    assert output_size(3840, 2160, None) == (3840, 2160)
    # never upscale
    assert output_size(1920, 1080, 1440) == (1920, 1080)
    # odd aspect stays even-dimensioned
    assert output_size(3834, 2160, 1440)[0] % 2 == 0


def test_recent_laps_last_five():
    df = make_session_df(duration_s=500.0)
    laps = [(n, n * 45.0, (n + 1) * 45.0) for n in range(8)]  # 8 x 45 s laps
    tl = sample_timeline(df, laps, video_offset_s=0.0, start_s=0.0, duration_s=420.0, fps=1.0)
    f = tl.frames[-1]  # t=419: laps 0..8*45? -> laps ending <= 419: laps 0..7 end at 360
    nums = [n for n, _, _ in f.recent_laps]
    assert len(nums) == 5
    assert nums == sorted(nums)  # oldest first
    assert nums[-1] == max(nums)  # newest is the latest completed lap
    assert all(d == pytest.approx(45.0) and ok for _, d, ok in f.recent_laps)

    early = tl.frames[100]  # t=100 -> only laps 0 and 1 completed
    assert [n for n, _, _ in early.recent_laps] == [0, 1]


def test_min_lap_invalidates_too_fast_laps():
    df = make_session_df(duration_s=300.0)
    # Lap 2 is a 38 s "lap" (cut track); circuit minimum is 42 s.
    laps = [(1, 0.0, 45.8), (2, 45.8, 83.8), (3, 83.8, 129.1), (4, 129.1, 173.5)]
    tl = sample_timeline(
        df, laps, video_offset_s=0.0, start_s=0.0, duration_s=250.0, fps=1.0, min_lap_s=42.0
    )
    f = tl.frames[-1]
    by_num = {n: (d, ok) for n, d, ok in f.recent_laps}
    assert by_num[2][1] is False  # flagged invalid
    assert by_num[1][1] and by_num[3][1] and by_num[4][1]
    # Best ignores the 38 s lap: best is lap 4 (44.4 s).
    assert f.best_lap_s == pytest.approx(44.4)


def test_delta_vs_rolling_best():
    """Rolling reference: during lap N the delta compares against the best
    valid lap completed before lap N started."""
    import numpy as np

    hz = 10.0
    t = np.arange(0, 300, 1 / hz)
    # Constant 20 m/s except lap 2 runs 22 m/s (faster -> becomes the ref).
    laps = [(1, 0.0, 45.0), (2, 45.0, 86.0), (3, 86.0, 131.0)]
    speed = np.full_like(t, 20.0)
    speed[(t >= 45.0) & (t < 86.0)] = 22.0
    df = pd.DataFrame(
        {
            "t_s": t,
            "speed_ms": speed,
            "lat": -23.7 + 0.0001 * np.sin(t / 10),
            "lon": -46.69 + 0.0001 * np.cos(t / 10),
        }
    )
    tl = sample_timeline(df, laps, video_offset_s=0.0, start_s=0.0, duration_s=130.0, fps=1.0)

    # During lap 1 there is no completed reference -> no delta.
    assert tl.frames[20].delta_s is None
    # During lap 2 the ref is lap 1 (45 s @20 m/s = 900 m). Lap 2 runs 10%
    # faster, so 20 s in it has covered 440 m, where lap 1 needed 22 s:
    f_lap2 = tl.frames[65]  # t=65 -> 20 s into lap 2
    assert f_lap2.delta_s == pytest.approx(-2.0, abs=0.15)
    # ... and carries +2 m/s = +7.2 km/h over the reference.
    assert f_lap2.speed_delta_kmh == pytest.approx(7.2, abs=0.5)
    assert f_lap2.prev_lap == (1, pytest.approx(45.0), True)
    # During lap 3 (20 m/s again) the ref is lap 2 (faster): delta positive.
    f_lap3 = tl.frames[110]  # 24 s into lap 3
    assert f_lap3.delta_s > 1.5
    assert f_lap3.speed_delta_kmh == pytest.approx(-7.2, abs=0.5)
    assert f_lap3.best_lap_num == 2
    assert f_lap3.prev_lap == (2, pytest.approx(41.0), True)


def test_best_lap_ignores_truncated_fragments():
    df = make_session_df(duration_s=200.0)
    # Real laps ~45 s plus a 17 s fragment from a session cut mid-lap.
    laps = [(0, 0.0, 48.0), (1, 48.0, 93.0), (2, 93.0, 137.8), (3, 137.8, 154.9)]
    tl = sample_timeline(df, laps, video_offset_s=0.0, start_s=0.0, duration_s=200.0, fps=1.0)
    # After everything completed, best must be lap 2 (44.8 s), not the fragment.
    assert tl.frames[-1].best_lap_s == pytest.approx(44.8)


def test_track_outline_frac_bounds():
    lat = np.array([-23.7010, -23.7015, -23.7020, -23.7010])
    lon = np.array([-46.6970, -46.6980, -46.6975, -46.6970])
    pts = track_outline_frac(lat, lon)
    assert pts.shape == (4, 2)
    assert pts.min() >= 0.0 and pts.max() <= 1.0


def test_render_frame_produces_content():
    track = track_outline_frac(
        np.array([-23.7010, -23.7015, -23.7020]), np.array([-46.6970, -46.6980, -46.6975])
    )
    r = OverlayRenderer(1280, 720, track_frac=track)
    img = r.render_frame(
        FrameValues(
            t_video_s=0.0,
            speed_kmh=87.2,
            rpm=12345,
            water_temp=56.0,
            lap_num=3,
            lap_time_s=42.1,
            best_lap_s=61.87,
            pos_frac=(0.5, 0.5),
            g_lat=1.4,
            g_lon=-0.6,
            steering_deg=-35.0,
        )
    )
    assert img.size == (1280, 720) and img.mode == "RGBA"
    alpha = np.asarray(img)[:, :, 3]
    assert (alpha > 0).sum() > 5000  # widgets actually drawn


def test_awaiting_chrome_shows_gauges_without_data():
    """With day channels declared, gauge chrome renders from frame one even
    when the frame has no data; legacy mode (channels=None) stays minimal."""
    empty = FrameValues(t_video_s=0.0)
    chrome = OverlayRenderer(1280, 720, channels={"speed", "g", "steering"})
    legacy = OverlayRenderer(1280, 720)
    a_chrome = (np.asarray(chrome.render_frame(empty))[:, :, 3] > 0).sum()
    a_legacy = (np.asarray(legacy.render_frame(empty))[:, :, 3] > 0).sum()
    assert a_chrome > a_legacy + 5000  # speedo face, G rings, wheel all drawn

    # ... and values populate the same renderer instance when data arrives.
    live = chrome.render_frame(
        FrameValues(t_video_s=1.0, speed_kmh=58.0, g_lat=0.2, g_lon=0.1, steering_deg=-20.0)
    )
    assert (np.asarray(live)[:, :, 3] > 0).sum() > a_chrome  # dot + needle + digits


def test_delta_bar_center_anchored_rules():
    """Delta bars grow from the scale midpoint: green to the RIGHT when the
    delta favors the driver, red to the LEFT when against. Time quantizes to
    0.01 s over +/-1 s; speed to 0.1 km/h over +/-5 km/h."""
    from media_tools.overlay import delta_bar

    # time delta (negative = faster = good): -0.86 s -> green right, 86% long
    q, frac, right, good = delta_bar(-0.86, span=1.0, step=0.01, positive_is_good=False)
    assert (q, right, good) == (pytest.approx(-0.86), True, True)
    assert frac == pytest.approx(0.86)
    # +0.06 s (slower) -> red bar to the left
    q, frac, right, good = delta_bar(0.06, span=1.0, step=0.01, positive_is_good=False)
    assert (right, good) == (False, False)
    assert frac == pytest.approx(0.06)
    # quantization: 0.004 s rounds to 0.00 -> no bar, normalized zero
    q, frac, _, _ = delta_bar(0.004, span=1.0, step=0.01, positive_is_good=False)
    assert q == 0.0 and frac == 0.0 and format(q, "+.2f") == "+0.00"
    # only the BAR saturates at the span: -3.7 s shows a full-length bar but
    # the number keeps reporting the true value
    q, frac, right, good = delta_bar(-3.7, span=1.0, step=0.01, positive_is_good=False)
    assert q == pytest.approx(-3.7) and frac == 1.0 and right and good
    assert format(q, "+.2f") == "-3.70"
    # speed side: +7.83 -> bar pinned full right, number reads whole km/h
    q, frac, right, good = delta_bar(7.83, span=5.0, step=0.1, positive_is_good=True)
    assert q == pytest.approx(7.8) and frac == 1.0 and right and good
    assert format(q, "+.0f") == "+8"

    # speed delta (positive = faster = good): +1.23 -> 1.2, green right, 24%
    q, frac, right, good = delta_bar(1.23, span=5.0, step=0.1, positive_is_good=True)
    assert q == pytest.approx(1.2) and right and good
    assert frac == pytest.approx(0.24)
    # -2.34 -> quantized -2.3, red left at 46%
    q, frac, right, good = delta_bar(-2.34, span=5.0, step=0.1, positive_is_good=True)
    assert q == pytest.approx(-2.3) and not right and not good
    assert frac == pytest.approx(0.46)


def test_replace_with_retry_survives_transient_lock(tmp_path, monkeypatch):
    """A transiently locked destination must not discard a finished render."""
    from media_tools.render import _replace_with_retry

    src = tmp_path / "new.mp4"
    dst = tmp_path / "final.mp4"
    src.write_bytes(b"new render")
    dst.write_bytes(b"old render")

    real_replace = Path.replace
    calls = {"n": 0}

    def flaky_replace(self, target):
        calls["n"] += 1
        if calls["n"] < 3:  # locked for the first two attempts
            raise PermissionError(5, "Acesso negado")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    _replace_with_retry(src, dst, attempts=5, delay_s=0.01)
    assert dst.read_bytes() == b"new render"
    assert not src.exists()

    # A permanent lock keeps the finished file and reports where it is.
    src2 = tmp_path / "new2.mp4"
    src2.write_bytes(b"x")
    monkeypatch.setattr(
        Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError(5, "locked"))
    )
    with pytest.raises(RuntimeError, match="does NOT need to repeat"):
        _replace_with_retry(src2, dst, attempts=2, delay_s=0.01)
    assert src2.exists()


def test_g_and_steering_widgets_optional():
    r = OverlayRenderer(1280, 720)
    with_g = r.render_frame(FrameValues(t_video_s=0.0, g_lat=1.0, g_lon=0.5, steering_deg=20.0))
    without = r.render_frame(FrameValues(t_video_s=0.0))
    # The G-ball and steering wheel add visible pixels somewhere on frame.
    a_with = (np.asarray(with_g)[:, :, 3] > 0).sum()
    a_without = (np.asarray(without)[:, :, 3] > 0).sum()
    assert a_with > a_without + 3000


def make_session_df(duration_s=120.0, hz=10.0):
    t = np.arange(0, duration_s, 1 / hz)
    return pd.DataFrame(
        {
            "t_s": t,
            "rpm": 9000 + 4000 * np.sin(t / 5),
            "speed_ms": 20 + 8 * np.sin(t / 7),
            "water_temp": np.full_like(t, 55.0),
            "lat": -23.70 - 0.001 * np.sin(t / 10),
            "lon": -46.69 - 0.001 * np.cos(t / 10),
        }
    )


def test_sample_timeline_values_and_laps():
    df = make_session_df()
    laps = [(1, 0.0, 50.0), (2, 50.0, 95.0)]
    # video starts 10 s into the session, 30 s long, 2 fps
    tl = sample_timeline(df, laps, video_offset_s=10.0, start_s=0.0, duration_s=30.0, fps=2.0)
    assert len(tl.frames) == 60
    f0 = tl.frames[0]  # session t=10
    assert f0.lap_num == 1 and f0.lap_time_s == pytest.approx(10.0)
    assert f0.speed_kmh == pytest.approx((20 + 8 * np.sin(10 / 7)) * 3.6, rel=1e-3)
    f_last = tl.frames[-1]  # session t=39.5
    assert f_last.lap_num == 1
    # frame at video t=25 -> session t=35... still lap1; check lap 2 entry:
    tl2 = sample_timeline(df, laps, video_offset_s=45.0, start_s=0.0, duration_s=20.0, fps=2.0)
    f10 = tl2.frames[20]  # video t=10 -> session t=55
    assert f10.lap_num == 2
    assert f10.best_lap_s == pytest.approx(50.0)  # lap 1 completed in 50 s


def test_sample_timeline_outside_coverage_is_none():
    df = make_session_df(duration_s=60.0)
    tl = sample_timeline(df, [], video_offset_s=-30.0, start_s=0.0, duration_s=20.0, fps=1.0)
    # video starts 30 s before telemetry: all sampled frames uncovered
    assert all(f.speed_kmh is None and f.rpm is None for f in tl.frames)


def have_ffmpeg() -> bool:
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not available")
def test_render_day_end_to_end(cfg, tmp_path, monkeypatch):
    """Real ffmpeg: 4 s test clip + synthetic telemetry -> overlaid output."""
    lib_root = cfg.library_root
    day = date(2026, 7, 12)
    day_dir = lib_root / day.isoformat()
    (day_dir / "raw" / "video").mkdir(parents=True)
    (day_dir / "raw" / "telemetry").mkdir(parents=True)

    clip_path = day_dir / "raw" / "video" / "DJI_test.MP4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=4",
            "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(clip_path),
        ],
        check=True,
        capture_output=True,
    )

    start = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    session = TrackSession(id=1, start_utc=start, end_utc=start + timedelta(seconds=120))
    manifest = DayManifest(
        date=day,
        sessions=[session],
        telemetry=[
            TelemetryLog(
                file="raw/telemetry/s.xrk", source_name="s.xrk", size_bytes=1,
                start_utc=start, end_utc=session.end_utc, session_id=1,
                laps=[Lap(num=1, start_s=0.0, end_s=50.0), Lap(num=2, start_s=50.0, end_s=95.0)],
            )
        ],
        videos=[
            VideoClip(
                file="raw/video/DJI_test.MP4", source_name="DJI_test.MP4", size_bytes=1,
                duration_s=4.0, start_utc_estimate=start + timedelta(seconds=10),
                session_id=1,
                sync=SyncInfo(
                    video_start_utc=start + timedelta(seconds=10),
                    confidence=0.9, method="manual",
                ),
            )
        ],
    )

    from media_tools.telemetry import DayFrame

    monkeypatch.setattr(
        "media_tools.render.load_day_frame",
        lambda day_dir, manifest: DayFrame(
            df=make_session_df(),
            start_utc=start,
            laps=[(1, 0.0, 50.0), (2, 50.0, 95.0)],
        ),
    )

    report = render_day(cfg, manifest, day_dir)
    assert any(line.startswith("+") for line in report), report
    out = day_dir / "out" / "DJI_test_overlay.mp4"
    assert out.is_file() and out.stat().st_size > 10_000
    assert manifest.renders and manifest.renders[0].kind == "session"

    # idempotent second run
    report2 = render_day(cfg, manifest, day_dir)
    assert any(line.startswith("=") for line in report2)
