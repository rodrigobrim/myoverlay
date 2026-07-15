"""Sync tests: synthesize telemetry RPM and matching engine audio with a known
offset, and check the cross-correlation recovers it."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from media_tools.sync import (
    AUDIO_RATE,
    FEATURE_HZ,
    audio_feature,
    find_offset,
    rpm_feature,
)


def synth_rpm_profile(duration_s: float, seed: int = 7) -> np.ndarray:
    """A kart-like RPM trace at FEATURE_HZ: corner/straight cycles + noise."""
    rng = np.random.default_rng(seed)
    n = int(duration_s * FEATURE_HZ)
    t = np.arange(n) / FEATURE_HZ
    p_lap = rng.uniform(33.0, 55.0)
    p_corner = rng.uniform(5.0, 12.0)
    rpm = (
        10500
        + 3200 * np.sin(2 * np.pi * t / p_lap + rng.uniform(0, 6.28))   # lap rhythm
        + 1500 * np.sin(2 * np.pi * t / p_corner + rng.uniform(0, 6.28))  # corners
        + rng.normal(0, 40, n).cumsum() * 0.05                          # mild drift
        + rng.normal(0, 150, n)                                         # jitter
    )
    rpm = np.clip(rpm, 4500, 15800)
    kernel = np.hanning(11)
    return np.convolve(rpm, kernel / kernel.sum(), mode="same")


def synth_audio_from_rpm(
    rpm_at_10hz: np.ndarray, start_s: float = 0.0, duration_s: float | None = None
) -> np.ndarray:
    """Engine-ish audio: tone at firing frequency, loudness follows RPM.

    start_s/duration_s cut the clip at sample-accurate (non-grid) times so
    tests can use true offsets that don't sit on any feature grid.
    """
    total_s = len(rpm_at_10hz) / FEATURE_HZ
    duration_s = duration_s if duration_s is not None else total_s - start_s
    n_audio = int(duration_s * AUDIO_RATE)
    t = start_s + np.arange(n_audio) / AUDIO_RATE
    rpm = np.interp(t, np.arange(len(rpm_at_10hz)) / FEATURE_HZ, rpm_at_10hz)
    phase = np.cumsum(rpm / 60.0 * 2) * 2 * np.pi / AUDIO_RATE  # 2nd harmonic
    amp = 0.2 + 0.8 * (rpm - rpm.min()) / (np.ptp(rpm) + 1e-9)
    rng = np.random.default_rng(3)
    return (amp * np.sin(phase) + 0.05 * rng.normal(size=n_audio)).astype(np.float32)


def test_find_offset_recovers_known_lag():
    telemetry_rpm = synth_rpm_profile(300)  # 5 min session
    true_lag_s = 84.3  # video starts 84.3 s into the session
    clip_s = 120.0

    i0 = int(true_lag_s * FEATURE_HZ)
    clip_rpm = telemetry_rpm[i0 : i0 + int(clip_s * FEATURE_HZ)]
    audio = synth_audio_from_rpm(clip_rpm)

    df = pd.DataFrame(
        {
            "t_s": np.arange(len(telemetry_rpm)) / FEATURE_HZ,
            "rpm": telemetry_rpm,
        }
    )
    result = find_offset(
        rpm_feature(df),
        audio_feature(audio),
        lag_window_s=(true_lag_s - 120, true_lag_s + 120),
    )
    assert result.lag_s == pytest.approx(true_lag_s, abs=0.2)
    assert result.confidence > 0.5


@pytest.mark.parametrize(
    "seed,true_lag_s",
    [(7, 84.3123), (11, 33.017), (23, 140.5049), (31, 99.9871)],  # off-grid truths
)
def test_multi_stage_sync_within_5ms(seed, true_lag_s):
    """Full estimator must land within 5 ms of an off-grid truth."""
    from media_tools.sync import estimate_offset

    telemetry_rpm = synth_rpm_profile(300, seed=seed)
    audio = synth_audio_from_rpm(telemetry_rpm, start_s=true_lag_s, duration_s=120.0)

    df = pd.DataFrame(
        {"t_s": np.arange(len(telemetry_rpm)) / FEATURE_HZ, "rpm": telemetry_rpm}
    )
    result = estimate_offset(df, audio, est_lag_s=true_lag_s + 40.0, clock_tolerance_s=120.0)
    assert result.confidence > 0.5
    assert abs(result.lag_s - true_lag_s) <= 0.005, (
        f"sync error {abs(result.lag_s - true_lag_s)*1000:.2f} ms exceeds 5 ms"
    )


def test_rev_kicks_do_not_define_sync_point():
    """Short pit rev 'kicks' must not become the lock point; the sync must
    key on sustained acceleration and still hit 5 ms."""
    from media_tools.sync import estimate_offset

    rng = np.random.default_rng(3)
    n_pit = int(90 * FEATURE_HZ)
    pit = np.full(n_pit, 1800.0) + rng.normal(0, 30, n_pit)
    for k0 in (20.0, 45.0, 70.0):  # three 1.5 s rev kicks in the pits
        i0 = int(k0 * FEATURE_HZ)
        pit[i0 : i0 + int(1.5 * FEATURE_HZ)] = 9000.0
    drive = synth_rpm_profile(300, seed=13)
    rpm_all = np.concatenate([pit, drive])
    df = pd.DataFrame({"t_s": np.arange(len(rpm_all)) / FEATURE_HZ, "rpm": rpm_all})

    true_lag = 30.0  # video starts mid-pit, covering the kicks
    audio = synth_audio_from_rpm(rpm_all, start_s=true_lag, duration_s=260.0)
    result = estimate_offset(df, audio, true_lag + 20.0, 120.0)
    assert result.confidence > 0.5
    assert abs(result.lag_s - true_lag) <= 0.005


def test_find_offset_low_confidence_on_unrelated_audio():
    telemetry_rpm = synth_rpm_profile(300, seed=1)
    unrelated = synth_rpm_profile(120, seed=99)
    audio = synth_audio_from_rpm(unrelated[: int(60 * FEATURE_HZ)])

    df = pd.DataFrame(
        {"t_s": np.arange(len(telemetry_rpm)) / FEATURE_HZ, "rpm": telemetry_rpm}
    )
    result = find_offset(rpm_feature(df), audio_feature(audio))
    # Unrelated audio must never look like a confident match.
    assert result.confidence < 0.5


def test_engine_column_falls_back_to_speed():
    from media_tools.sync import engine_column

    # Real case: RPM lead not connected -> unified_frame drops rpm; GPS
    # speed carries the engine trace (direct-drive kart).
    df = pd.DataFrame({"t_s": [0.0, 1.0, 2.0], "speed_ms": [1.0, 5.0, 9.0]})
    assert engine_column(df) == "speed_ms"
    df2 = pd.DataFrame(
        {"t_s": [0.0, 1.0], "rpm": [8000.0, 9000.0], "speed_ms": [1.0, 2.0]}
    )
    assert engine_column(df2) == "rpm"


def test_rpm_feature_requires_engine_channel():
    df = pd.DataFrame({"t_s": [0.0, 1.0], "lat": [1.0, 2.0]})
    with pytest.raises(ValueError, match="no usable engine channel"):
        rpm_feature(df)


def test_envelope_offset_finds_stint_pattern():
    """Driving/pit on-off structure must sync even with featureless audio."""
    from media_tools.sync import AUDIO_RATE, envelope_offset

    rng = np.random.default_rng(2)
    # Day: 100 s driving, 300 s pit, 250 s driving, 150 s pit, 200 s driving.
    seconds = [(100, True), (300, False), (250, True), (150, False), (200, True)]
    speed = np.concatenate(
        [
            (18 + 5 * np.sin(np.arange(int(s * FEATURE_HZ)) / 40)) * on
            for s, on in seconds
        ]
    )
    df = pd.DataFrame({"t_s": np.arange(len(speed)) / FEATURE_HZ, "speed_ms": speed})

    # Clip: 400 s starting 60 s into the day; loudness follows speed.
    true_lag = 60.0
    n = int(400 * AUDIO_RATE)
    t = true_lag + np.arange(n) / AUDIO_RATE
    amp = 0.1 + 0.9 * np.interp(t, df["t_s"], speed) / 25.0
    pcm = (amp * rng.normal(0, 0.3, n)).astype(np.float32)

    res = envelope_offset(df, pcm)
    assert res.confidence > 0.5
    assert res.lag_s == pytest.approx(true_lag, abs=5.0)


def test_sync_day_seeds_unsynced_clips(cfg, tmp_path, monkeypatch):
    from datetime import date

    import media_tools.sync as sync_mod
    from media_tools.library import DayManifest, TelemetryLog, TrackSession, VideoClip
    from media_tools.sync import SyncResult
    from media_tools.telemetry import DayFrame

    day_dir = tmp_path / "day"
    day_dir.mkdir()
    start = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    session = TrackSession(id=1, start_utc=start, end_utc=start + timedelta(minutes=20))
    m = DayManifest(date=date(2026, 7, 12), sessions=[session])
    m.telemetry = [
        TelemetryLog(
            file="raw/telemetry/s.xrk",
            source_name="s.xrk",
            size_bytes=1,
            start_utc=start,
            end_utc=session.end_utc,
            session_id=1,
        )
    ]
    m.videos = [
        VideoClip(
            file="raw/video/a.MP4", source_name="a.MP4", size_bytes=1,
            duration_s=60, start_utc_estimate=start + timedelta(seconds=30), session_id=1,
        ),
        VideoClip(
            file="raw/video/b.MP4", source_name="b.MP4", size_bytes=1,
            duration_s=60, start_utc_estimate=start + timedelta(minutes=5), session_id=1,
        ),
    ]

    fake_day = DayFrame(
        df=pd.DataFrame({"t_s": [0.0, 1.0], "speed_ms": [1.0, 2.0]}),
        start_utc=start,
        laps=[],
    )
    monkeypatch.setattr("media_tools.telemetry.load_day_frame", lambda d, m2: fake_day)
    monkeypatch.setattr(sync_mod, "extract_audio_pcm", lambda p: p)  # pass path through

    calls = {"n": 0}

    def fake_estimate(df, pcm, est_lag_s=None, clock_tolerance_s=None):
        calls["n"] += 1
        if "a.MP4" in str(pcm):
            return SyncResult(lag_s=60.0, confidence=0.8, method="fine")
        raise RuntimeError("audio broken")

    monkeypatch.setattr(sync_mod, "estimate_offset", fake_estimate)

    report = sync_mod.sync_day(cfg, m, day_dir)
    assert calls["n"] == 2
    # clip a: video_start = day_start + 60 s; estimate was day_start + 30 s
    assert m.videos[0].sync.method == "audio-fine"
    assert m.videos[0].sync.video_start_utc == start + timedelta(seconds=60)
    assert m.videos[1].sync.method == "seeded"
    drift = (m.videos[1].sync.video_start_utc - m.videos[1].start_utc_estimate).total_seconds()
    assert drift == pytest.approx(30.0)
    assert any("seeded" in line for line in report)
