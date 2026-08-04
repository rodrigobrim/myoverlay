import numpy as np
import pytest

from media_tools.library import RaceEnd
from media_tools.raceend import RACE_END_BUFFER_S, detect_race_end
from media_tools.sync import AUDIO_RATE, detect_engine_shutdown


def engine_pcm(seconds: float, seed: int = 1) -> np.ndarray:
    """Loud engine-ish audio: 200 Hz tone + noise."""
    rng = np.random.default_rng(seed)
    n = int(seconds * AUDIO_RATE)
    t = np.arange(n) / AUDIO_RATE
    return (0.6 * np.sin(2 * np.pi * 200 * t) + 0.05 * rng.normal(size=n)).astype(np.float32)


def quiet_pcm(seconds: float, seed: int = 2) -> np.ndarray:
    """Ambient noise only (engine off)."""
    rng = np.random.default_rng(seed)
    return (0.01 * rng.normal(size=int(seconds * AUDIO_RATE))).astype(np.float32)


def test_shutdown_detected_at_transition():
    pcm = np.concatenate([engine_pcm(120.0), quiet_pcm(60.0)])
    stop = detect_engine_shutdown(pcm)
    assert stop is not None
    assert stop == pytest.approx(120.0, abs=2.0)


def test_engine_running_to_the_end_means_no_shutdown():
    assert detect_engine_shutdown(engine_pcm(120.0)) is None


def test_short_quiet_tail_is_not_a_shutdown():
    # 10 s of quiet at the end: could be a coast/stall, not a shutdown.
    pcm = np.concatenate([engine_pcm(120.0), quiet_pcm(10.0)])
    assert detect_engine_shutdown(pcm, min_off_s=20.0) is None


def test_flickery_quiet_tail_is_still_a_shutdown():
    """Real pit-lane tails have brief loud blips (voices, karts passing the
    parked kart). Blips < ~3 s must not break the quiet run - this is the
    exact failure observed on the real 07-13 clip."""
    rng = np.random.default_rng(7)
    tail = quiet_pcm(70.0)
    for blip_at in (15.0, 32.0, 55.0):  # 1 s bursts inside the quiet tail
        i0 = int(blip_at * AUDIO_RATE)
        n = int(1.0 * AUDIO_RATE)
        t = np.arange(n) / AUDIO_RATE
        tail[i0 : i0 + n] += (0.5 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)
    pcm = np.concatenate([engine_pcm(120.0), tail])
    stop = detect_engine_shutdown(pcm)
    assert stop is not None
    assert stop == pytest.approx(120.0, abs=3.0)


def test_mid_clip_quiet_spell_is_ignored():
    # Pit stop in the middle, then driving to the end: no shutdown.
    pcm = np.concatenate([engine_pcm(60.0), quiet_pcm(40.0), engine_pcm(60.0, seed=3)])
    assert detect_engine_shutdown(pcm) is None


def test_detect_race_end_cut_math(tmp_path):
    """Cut = last finish-line crossing (telemetry) -> video time, + buffer."""
    # Day laps (telemetry time), deliberately out of order: the anchor is the
    # MAX lap end, not the last list element. video_offset_s=100 ->
    # last crossing tel 790 -> video 690 -> +15 = 705.
    laps = [(3, 700.0, 745.0), (1, 100.0, 145.0), (4, 745.0, 790.0), (2, 145.0, 190.0)]
    result = detect_race_end(tmp_path / "v.mp4", laps, 100.0, 2000.0)
    assert result.cut_at_s == pytest.approx(790.0 - 100.0 + RACE_END_BUFFER_S)
    assert result.engine_stop_s is None  # audio scan removed: never set


def test_detect_race_end_no_laps(tmp_path):
    result = detect_race_end(tmp_path / "v.mp4", [], 0.0, 2000.0)
    assert result == RaceEnd()  # no laps: nothing to anchor a cut to
    assert result.cut_at_s is None and result.engine_stop_s is None


def test_detect_race_end_cut_beyond_clip_is_dropped(tmp_path):
    # Crossing at tel 95 -> video 95 -> +15 = 110 > clip 105: no-op trim.
    assert detect_race_end(tmp_path / "v.mp4", [(1, 50.0, 95.0)], 0.0, 105.0).cut_at_s is None
    # Boundary: cut exactly at clip duration is also a no-op (strict <).
    assert detect_race_end(tmp_path / "v.mp4", [(1, 50.0, 90.0)], 0.0, 105.0).cut_at_s is None


def test_detect_race_end_lap_before_clip_start_is_dropped(tmp_path):
    # Last crossing at tel 90 but the video starts at tel 200: cut <= 0.
    assert detect_race_end(tmp_path / "v.mp4", [(1, 45.0, 90.0)], 200.0, 2000.0).cut_at_s is None


def test_detect_race_end_custom_buffer(tmp_path):
    result = detect_race_end(tmp_path / "v.mp4", [(1, 0.0, 45.0)], 0.0, 100.0, buffer_s=5.0)
    assert result.cut_at_s == pytest.approx(50.0)


def test_config_flag_hyphenated_alias(tmp_path, monkeypatch):
    from media_tools.config import RenderConfig, load_config

    assert RenderConfig().scan_video_for_race_end is True  # default on

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        'library_root = "%s"\n[render]\n"scan-video-for-race-end" = false\n'
        % str(tmp_path).replace("\\", "/"),
        encoding="utf-8",
    )
    cfg = load_config(cfg_file)
    assert cfg.render.scan_video_for_race_end is False
