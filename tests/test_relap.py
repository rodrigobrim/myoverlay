"""Start/finish re-lapping from GPS position.

Every test that was run by hand against the real `kgv e2_Race_a_0096.xrk`
while building the `start-finish-line` re-lapping is reproduced here on a
synthetic circular track, so the behaviour is pinned without the real
telemetry file:

  - each lap boundary is found by MAP POSITION (the GPS track crossing the
    S/F line), interpolated between fixes;
  - a kart idling on the line does not trip it, however much it jitters;
  - passing beyond the end of the line (pit lane) is not a crossing;
  - the crossing keeps only the racing direction;
  - moving the line shifts every boundary;
  - the S/F coordinate can be recovered back out of the crossings.
"""

import math

import numpy as np
import pytest

from media_tools.relap import (
    crossings_by_line,
    laps_from_crossings,
    line_length_m,
    sf_from_crossings,
)

BASE_LAT, BASE_LON = -23.6042, -46.8368  # near Granja Viana
R_M = 100.0                              # track radius (m)
LAP_S = 60.0                             # one lap
DT = 0.1                                 # 10 Hz GPS, like the MyChron
M_PER_DEG = 111320.0
LON_M = M_PER_DEG * math.cos(math.radians(BASE_LAT))


def to_latlon(x: float, y: float) -> tuple[float, float]:
    return BASE_LAT + y / M_PER_DEG, BASE_LON + x / LON_M


def circular_track(laps: int, start_angle: float = 0.0):
    """A kart going `laps` times around a circle of radius R_M at constant
    speed, sampled at 10 Hz. Returns (t_s, lat, lon). At angle 0 the kart is
    at the east point (R_M, 0); it starts there when start_angle == 0 - i.e.
    sitting on the S/F, exactly like the real grid start."""
    # A short tail past the last lap so a crossing that falls on the final
    # sample still has a fix after it (a recording never stops exactly on
    # the line).
    t = np.arange(0.0, laps * LAP_S + 2.0, DT)
    ang = start_angle + 2 * math.pi * t / LAP_S
    x = R_M * np.cos(ang)
    y = R_M * np.sin(ang)
    lat = BASE_LAT + y / M_PER_DEG
    lon = BASE_LON + x / LON_M
    return t, lat, lon


def line_at_angle(angle: float, width_m: float = 8.0):
    """The S/F line laid ACROSS the track at `angle`: a segment of
    `width_m` centred on the circle, perpendicular to the direction of
    travel (i.e. radial)."""
    cx, cy = R_M * math.cos(angle), R_M * math.sin(angle)
    half = width_m / 2
    return (
        to_latlon(cx - half * math.cos(angle), cy - half * math.sin(angle)),
        to_latlon(cx + half * math.cos(angle), cy + half * math.sin(angle)),
    )


def test_line_length_is_the_gate_width():
    assert line_length_m(line_at_angle(0.0, width_m=8.0)) == pytest.approx(8.0, abs=0.05)


def test_crossing_per_lap_by_position():
    """One crossing per completed lap, on the S/F line, ~LAP_S apart."""
    t, lat, lon = circular_track(laps=4)
    cross = crossings_by_line(t, lat, lon, line_at_angle(0.0))
    # The kart starts ON the line and drives away from it, so the passes are
    # the 4 lap completions.
    assert len(cross) == 4
    assert cross == pytest.approx([60.0, 120.0, 180.0, 240.0], abs=0.05)


def test_crossing_time_is_interpolated_between_fixes():
    """The gate resolves below the 10 Hz sample spacing: with the line a
    third of a sample interval past a fix, the crossing time follows."""
    t, lat, lon = circular_track(laps=2)
    # A quarter of a sample step in angle past the east point.
    quarter = 2 * math.pi * (0.25 * DT) / LAP_S
    cross = crossings_by_line(t, lat, lon, line_at_angle(quarter))
    assert cross[0] == pytest.approx(LAP_S + 0.25 * DT, abs=0.01)


def test_idling_on_the_line_does_not_trip_it():
    """A kart standing on the line with GPS jitter (metre-scale, the real
    figure) produces no crossings: the gate needs the kart to be moving."""
    rng = np.random.default_rng(7)
    t = np.arange(0.0, 60.0, DT)
    jitter_x = rng.normal(0.0, 1.5, len(t))
    jitter_y = rng.normal(0.0, 1.5, len(t))
    lat, lon = [], []
    for jx, jy in zip(jitter_x, jitter_y):
        la, lo = to_latlon(R_M + jx, jy)
        lat.append(la)
        lon.append(lo)
    assert crossings_by_line(t, np.array(lat), np.array(lon), line_at_angle(0.0)) == []


def test_launch_from_behind_the_line_is_kept():
    """A grid slot is BEHIND the line: the kart's first crossing comes a few
    metres into its first lap and is the race start - it must survive."""
    # Start a fifth of a lap before the line, drive 3 laps.
    t, lat, lon = circular_track(laps=3, start_angle=-2 * math.pi / 5)
    cross = crossings_by_line(t, lat, lon, line_at_angle(0.0))
    assert cross[0] == pytest.approx(LAP_S / 5, abs=0.1)
    assert len(cross) == 3


def test_passing_beyond_the_end_of_the_line_is_not_a_crossing():
    """The line spans the asphalt; a kart on a parallel path outside it (the
    pit lane) crosses the line's INFINITE extension but not the segment."""
    t, lat, lon = circular_track(laps=3)
    # A 4 m line offset 20 m inside the circle: the kart passes its
    # extension every lap but never the segment itself.
    inner = line_at_angle(0.0, width_m=4.0)
    shifted = tuple(to_latlon(R_M - 20.0 + (i * 4.0 - 2.0), 0.0) for i in (0, 1))
    assert line_length_m(shifted) == pytest.approx(4.0, abs=0.05)
    assert crossings_by_line(t, lat, lon, shifted) == []
    assert crossings_by_line(t, lat, lon, inner)  # the on-track line still fires


def test_only_the_racing_direction_counts():
    """Two laps forwards then one pass backwards (a kart pushed back down
    the straight): the wrong-way pass is not a lap boundary."""
    t, lat, lon = circular_track(laps=2)
    # Reverse straight run back down the line's own straight at 10 m/s:
    # the racing direction at angle 0 is +y, so this goes -y.
    elapsed = np.arange(0.0, 5.0, DT)
    back_t = t[-1] + DT + elapsed
    back = [to_latlon(R_M, 25.0 - 10.0 * e) for e in elapsed]
    back_lat = np.array([p[0] for p in back])
    back_lon = np.array([p[1] for p in back])
    all_t = np.concatenate([t, back_t])
    all_lat = np.concatenate([lat, back_lat])
    all_lon = np.concatenate([lon, back_lon])
    cross = crossings_by_line(all_t, all_lat, all_lon, line_at_angle(0.0))
    assert len(cross) == 2
    assert cross == pytest.approx([60.0, 120.0], abs=0.05)


def test_irregular_sampling_still_lands_on_the_line():
    """GPS fixes are not evenly spaced. With the samples randomly jittered in
    TIME (the position at each is still exact), the crossings must stay put -
    nothing may assume a fixed sample rate."""
    rng = np.random.default_rng(3)
    t, _, _ = circular_track(laps=3)
    # Keep a random 70% of the fixes: gaps of 0.1-0.5 s, like a real log.
    keep = np.sort(rng.choice(len(t), size=int(0.7 * len(t)), replace=False))
    t = t[keep]
    ang = 2 * math.pi * t / LAP_S
    lat = BASE_LAT + (R_M * np.sin(ang)) / M_PER_DEG
    lon = BASE_LON + (R_M * np.cos(ang)) / LON_M
    cross = crossings_by_line(t, lat, lon, line_at_angle(0.0))
    assert cross == pytest.approx([60.0, 120.0, 180.0], abs=0.05)


def test_a_hole_in_the_log_is_not_a_crossing():
    """The logger stops while the kart is parked behind the line and resumes
    once it is past: the two fixes straddle the line minutes apart. There is
    no evidence of when (or whether) the line was crossed, so no boundary is
    invented."""
    before = np.arange(0.0, 5.0, DT)
    after = np.arange(300.0, 305.0, DT)
    # Sitting 10 m before the line, then 10 m past it, both times crawling.
    pts = [to_latlon(R_M, -10.0 - 0.1 * e) for e in before]
    pts += [to_latlon(R_M, 10.0 + 0.1 * (e - 300.0)) for e in after]
    t = np.concatenate([before, after])
    lat = np.array([p[0] for p in pts])
    lon = np.array([p[1] for p in pts])
    assert crossings_by_line(t, lat, lon, line_at_angle(0.0)) == []


def test_moving_the_line_shifts_every_boundary():
    """Putting the S/F on the far side of the circle shifts each crossing by
    half a lap - the essence of correcting the early beacon pin."""
    t, lat, lon = circular_track(laps=4)
    near = crossings_by_line(t, lat, lon, line_at_angle(0.0))
    far = crossings_by_line(t, lat, lon, line_at_angle(math.pi))
    offsets = [f - n for f, n in zip(far, near)]
    assert all(o == pytest.approx(-30.0, abs=0.3) for o in offsets)


def test_sf_coordinate_recovered_from_crossings():
    """The pinned S/F can be read back out of its own crossings."""
    t, lat, lon = circular_track(laps=5)
    line = line_at_angle(0.0)
    cross = crossings_by_line(t, lat, lon, line)
    got_lat, got_lon = sf_from_crossings(t, lat, lon, cross)
    mid_lat, mid_lon = (line[0][0] + line[1][0]) / 2, (line[0][1] + line[1][1]) / 2
    assert got_lat == pytest.approx(mid_lat, abs=1e-5)
    assert got_lon == pytest.approx(mid_lon, abs=1e-5)


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


def test_no_crossings_when_track_never_reaches_the_line():
    """A line far from the track yields no crossings (render then keeps the
    native .xrk laps)."""
    t, lat, lon = circular_track(laps=2)
    far = ((BASE_LAT + 1.0, BASE_LON + 1.0), (BASE_LAT + 1.0001, BASE_LON + 1.0001))
    assert crossings_by_line(t, lat, lon, far) == []


def test_coincident_endpoints_rejected():
    t, lat, lon = circular_track(laps=1)
    same = ((BASE_LAT, BASE_LON), (BASE_LAT, BASE_LON))
    with pytest.raises(ValueError):
        crossings_by_line(t, lat, lon, same)
