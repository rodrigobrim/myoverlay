"""Start/finish re-lapping from GPS position.

Every test that was run by hand against the real `kgv e2_Race_a_0096.xrk`
while building the `start-finish-coordinate` re-lapping is reproduced here on
a synthetic circular track, so the behaviour is pinned without the real
telemetry file:

  - each lap boundary is found by MAP POSITION (GPS crossing the S/F point);
  - the grid-start proximity hit is excluded by DISTANCE, not a clock;
  - moving the S/F coordinate shifts every boundary;
  - the S/F coordinate can be recovered back out of the crossings.
"""

import math

import numpy as np
import pytest

from media_tools.relap import (
    crossings_by_coordinate,
    laps_from_crossings,
    sf_from_crossings,
)

BASE_LAT, BASE_LON = -23.6042, -46.8368  # near Granja Viana
R_M = 100.0                              # track radius (m)
LAP_S = 60.0                             # one lap
DT = 0.1                                 # 10 Hz GPS, like the MyChron


def circular_track(laps: int, start_angle: float = 0.0):
    """A kart going `laps` times around a circle of radius R_M at constant
    speed, sampled at 10 Hz. Returns (t_s, lat, lon). At angle 0 the kart is
    at the east point (R_M, 0); it starts there when start_angle == 0 - i.e.
    sitting on the S/F, exactly like the real grid start."""
    t = np.arange(0.0, laps * LAP_S + DT, DT)
    ang = start_angle + 2 * math.pi * t / LAP_S
    x = R_M * np.cos(ang)
    y = R_M * np.sin(ang)
    lat = BASE_LAT + y / 111320.0
    lon = BASE_LON + x / (111320.0 * math.cos(math.radians(BASE_LAT)))
    return t, lat, lon


def sf_at_angle(angle: float) -> tuple[float, float]:
    """The lat/lon of the point on the circle at `angle`."""
    x = R_M * math.cos(angle)
    y = R_M * math.sin(angle)
    return (
        BASE_LAT + y / 111320.0,
        BASE_LON + x / (111320.0 * math.cos(math.radians(BASE_LAT))),
    )


def test_crossing_per_lap_by_position():
    """One crossing per completed lap, at the S/F point, ~LAP_S apart."""
    t, lat, lon = circular_track(laps=4)
    sf_lat, sf_lon = sf_at_angle(0.0)  # start/finish at the east point
    cross = crossings_by_coordinate(t, lat, lon, sf_lat, sf_lon)
    # start sits on the S/F (t=0), so passes are at 0,1,2,3,4 laps; the t=0
    # grid hit is dropped, leaving the 4 lap completions.
    assert len(cross) == 4
    assert cross == pytest.approx([60.0, 120.0, 180.0, 240.0], abs=0.2)


def test_grid_start_excluded_by_distance_not_time():
    """The t=0 pass (kart idling on the S/F) covers no track and must be
    dropped even though it is a genuine closest-approach to the point."""
    t, lat, lon = circular_track(laps=3)
    sf_lat, sf_lon = sf_at_angle(0.0)
    cross = crossings_by_coordinate(t, lat, lon, sf_lat, sf_lon)
    assert min(cross) > LAP_S * 0.5   # nothing kept from the standing start
    assert len(cross) == 3


def test_moving_the_sf_shifts_every_boundary():
    """Putting the S/F on the far side of the circle (angle pi) shifts each
    crossing by half a lap - the essence of correcting the early pin."""
    t, lat, lon = circular_track(laps=4)
    near = crossings_by_coordinate(t, lat, lon, *sf_at_angle(0.0))
    far = crossings_by_coordinate(t, lat, lon, *sf_at_angle(math.pi))
    # far crossings fall at 30,90,150,210 s: offset ~ -30 s (half a lap) from
    # the near ones, and consistently so.
    offsets = [f - n for f, n in zip(far, near)]
    assert all(o == pytest.approx(-30.0, abs=0.3) for o in offsets)


def test_sf_coordinate_recovered_from_crossings():
    """The pinned S/F can be read back out of its own crossings."""
    t, lat, lon = circular_track(laps=5)
    sf_lat, sf_lon = sf_at_angle(0.0)
    cross = crossings_by_coordinate(t, lat, lon, sf_lat, sf_lon)
    got_lat, got_lon = sf_from_crossings(t, lat, lon, cross)
    assert got_lat == pytest.approx(sf_lat, abs=1e-5)
    assert got_lon == pytest.approx(sf_lon, abs=1e-5)


def test_lap_table_brackets_recording_and_shares_boundaries():
    """laps_from_crossings brackets the recording with an out-lap and in-lap
    and shares each crossing between adjacent laps (so complete/opened-lap
    filters classify them correctly)."""
    laps = laps_from_crossings([60_000, 120_000, 180_000], t_start=0, t_end=200_000)
    assert [lp["num"] for lp in laps] == [0, 1, 2, 3]
    assert laps[0] == {"num": 0, "start_time": 0.0, "end_time": 60_000.0}
    assert laps[-1]["end_time"] == 200_000.0
    # each internal boundary is one lap's end and the next lap's start
    assert laps[1]["start_time"] == laps[0]["end_time"]
    assert laps[2]["start_time"] == laps[1]["end_time"]


def test_no_crossings_when_track_never_reaches_point():
    """A S/F point far from the track yields no crossings (render then keeps
    the native .xrk laps)."""
    t, lat, lon = circular_track(laps=2)
    assert crossings_by_coordinate(t, lat, lon, BASE_LAT + 1.0, BASE_LON + 1.0) == []
