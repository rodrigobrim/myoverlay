"""The pipeline cuts laps at the configured start/finish LINE.

The chronometer (and delta, lap counter, best lap) must start where the kart
crosses the real painted line - not at the MyChron beacon pin (the .xrk lap
table, which trips ~7 s early on purpose) and not at any sync anchor. These
tests drive load_day_frame / session_laps_derived with a synthetic circular
track around the built-in KGV line and check the laps land on the line, with
the beacon laps only as a fallback.
"""

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from media_tools.config import TracksConfig
from media_tools.library import DayManifest, Lap, TelemetryLog, TrackSession
from media_tools import telemetry as telemetry_mod

# The built-in KGV start/finish line and a circular track through its midpoint.
KGV = TracksConfig()
SF_LINE = KGV.start_finish_for_position(-23.6045, -46.8365)
MID_LAT = (SF_LINE[0][0] + SF_LINE[1][0]) / 2
MID_LON = (SF_LINE[0][1] + SF_LINE[1][1]) / 2
R_M = 100.0
LAP_S = 60.0
M_PER_DEG = 111320.0
LON_M = M_PER_DEG * math.cos(math.radians(MID_LAT))


def circular_df(laps: float) -> pd.DataFrame:
    """A kart circling through the KGV line's midpoint at constant speed.

    The circle is centred R_M west of the line midpoint, so at angle 0 the
    kart is exactly on the line, travelling perpendicular to it (north).
    """
    t = np.arange(0.0, laps * LAP_S, 0.1)
    ang = 2 * math.pi * t / LAP_S
    x = R_M * (np.cos(ang) - 1.0)  # 0 at angle 0 (on the line)
    y = R_M * np.sin(ang)
    return pd.DataFrame(
        {
            "t_s": t,
            "lat": MID_LAT + y / M_PER_DEG,
            "lon": MID_LON + x / LON_M,
            "speed_ms": np.full(len(t), 2 * math.pi * R_M / LAP_S),
        }
    )


START = datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc)


def manifest_with_beacon_laps() -> DayManifest:
    """One log whose .xrk beacon laps trip 7 s EARLY (the real situation)."""
    early = [Lap(num=k, start_s=k * LAP_S - 7.0, end_s=(k + 1) * LAP_S - 7.0)
             for k in range(1, 3)]
    return DayManifest(
        date=date(2026, 7, 16),
        sessions=[TrackSession(id=1, start_utc=START, end_utc=START + timedelta(minutes=10))],
        telemetry=[
            TelemetryLog(
                file="raw/telemetry/s.xrk", source_name="s.xrk", size_bytes=1,
                start_utc=START, end_utc=START + timedelta(minutes=10),
                session_id=1, laps=early,
            )
        ],
    )


def test_day_frame_laps_land_on_the_line_not_the_beacon(tmp_path, monkeypatch):
    """With GPS and the configured line, every lap boundary is the LINE
    crossing - the early beacon laps are ignored."""
    monkeypatch.setattr(telemetry_mod, "load_unified", lambda p: circular_df(3.0))
    frame = telemetry_mod.load_day_frame(tmp_path, manifest_with_beacon_laps(), KGV)
    # Boundaries at the line crossings (60 s, 120 s), bracketed by out/in laps.
    bounds = sorted({b for _, st, e in frame.laps for b in (st, e)})
    assert bounds[1] == pytest.approx(60.0, abs=0.05)
    assert bounds[2] == pytest.approx(120.0, abs=0.05)
    # And decisively NOT the beacon's early boundaries (53 s / 113 s).
    assert all(abs(b - 53.0) > 1.0 for b in bounds)


def test_day_frame_falls_back_to_beacon_laps_without_tracks(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_mod, "load_unified", lambda p: circular_df(3.0))
    frame = telemetry_mod.load_day_frame(tmp_path, manifest_with_beacon_laps())
    assert [(st, e) for _, st, e in frame.laps] == [(53.0, 113.0), (113.0, 173.0)]


def test_day_frame_falls_back_when_no_line_is_near(tmp_path, monkeypatch):
    """A session driven at a circuit the config does not know keeps its
    beacon laps - it must not borrow another circuit's line."""
    df = circular_df(3.0)
    df["lat"] += 1.0  # ~111 km away from every configured line
    monkeypatch.setattr(telemetry_mod, "load_unified", lambda p: df)
    frame = telemetry_mod.load_day_frame(tmp_path, manifest_with_beacon_laps(), KGV)
    assert [(st, e) for _, st, e in frame.laps] == [(53.0, 113.0), (113.0, 173.0)]


def test_derived_parquet_still_overrides_the_line(tmp_path, monkeypatch):
    """A hand-made .sf-relapped.parquet stays the strongest source."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    (tmp_path / "raw" / "telemetry").mkdir(parents=True)
    pq.write_table(
        pa.table({"x": [0]}).replace_schema_metadata(
            {b"laps": b'[{"num": 0, "start_time": 0, "end_time": 41000}]'}
        ),
        tmp_path / "raw" / "telemetry" / "s.sf-relapped.parquet",
    )
    monkeypatch.setattr(telemetry_mod, "load_unified", lambda p: circular_df(3.0))
    frame = telemetry_mod.load_day_frame(tmp_path, manifest_with_beacon_laps(), KGV)
    assert [(st, e) for _, st, e in frame.laps] == [(0.0, 41.0)]


def test_session_laps_derived_uses_the_line_too(tmp_path, monkeypatch):
    """Best lap / titles read the same line-based laps as the overlay."""
    # session_laps_derived loads the log's GPS lazily, gated on the .xrk
    # existing (day frames pass the already-loaded frame instead).
    (tmp_path / "raw" / "telemetry").mkdir(parents=True)
    (tmp_path / "raw" / "telemetry" / "s.xrk").write_bytes(b"x")
    monkeypatch.setattr(telemetry_mod, "load_unified", lambda p: circular_df(3.0))
    manifest = manifest_with_beacon_laps()
    laps = telemetry_mod.session_laps_derived(
        tmp_path, manifest, manifest.sessions[0], KGV
    )
    bounds = sorted({b for _, st, e in laps for b in (st, e)})
    assert bounds[1] == pytest.approx(60.0, abs=0.05)
    # Without tracks: the raw beacon laps.
    raw = telemetry_mod.session_laps_derived(tmp_path, manifest, manifest.sessions[0])
    assert [(st, e) for _, st, e in raw] == [(53.0, 113.0), (113.0, 173.0)]
