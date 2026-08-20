"""Start/finish re-lapping from GPS position.

The MyChron pins the S/F beacon deliberately early, so the .xrk lap table
fires ~seconds before the real start/finish. These helpers re-derive lap
boundaries purely from where the GPS track crosses the start/finish LINE -
never from a clock.

The line is a real segment across the asphalt, given as its two end
coordinates (`[track] start_finish_line` in config.toml). A lap boundary is
the point where the segment between two consecutive GPS fixes intersects it,
with the crossing time linearly interpolated between those fixes.

Why a segment and not a radius: the MyChron logs GPS at ~10 Hz, so at racing
speed consecutive fixes are 1-3 m apart and NO fix ever lands within a
sub-metre radius of the line. Only an intersection test resolves the crossing
below the sample spacing; `LINE_TOLERANCE_M` (0.4 m) is just the band in which
a fix counts as sitting ON the line rather than on either side of it.

The standalone export uses these helpers to write
`<stem>.sf-relapped.parquet`, which the render then consumes
(telemetry._derived_laps). The render pipeline never calls this module
directly; it only reads the derived file.
"""

from __future__ import annotations

import math

import numpy as np

# Half-width of the "on the line" band: a fix closer than this to the line is
# neither side of it. Sub-metre, so the crossing is defined by the
# interpolated intersection, not by proximity.
LINE_TOLERANCE_M = 0.4

# A crossing only counts while the kart is actually driving. Stationary GPS
# jitter on the grid (the kart waiting a metre behind the line) otherwise
# flickers across the line and manufactures lap boundaries. 5 m/s (18 km/h)
# sits far below any real pass of the line and far above GPS wander.
MIN_CROSSING_SPEED_MS = 5.0

# Longest gap between the two fixes straddling the line that still counts as
# a crossing. GPS fixes are NOT evenly spaced - the logger records only while
# the kart moves, and satellite dropouts leave holes - so a pair of fixes can
# sit on either side of the line minutes apart. Interpolating across such a
# hole invents a crossing time (and the kart may have gone round the back of
# the line entirely), so the gate stays silent instead. Well above the
# 10-25 Hz nominal spacing, so real crossings are never affected.
MAX_FIX_GAP_S = 1.0


def _local_xy(lat, lon, lat0: float, lon0: float):
    """Equirectangular metres east/north of (lat0, lon0) - exact enough at
    kart-track scale."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    x = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 111320.0
    return x, y


def line_midpoint(line) -> tuple[float, float]:
    """(lat, lon) halfway along the S/F line - its single-point stand-in."""
    (lat_a, lon_a), (lat_b, lon_b) = line
    return (lat_a + lat_b) / 2.0, (lon_a + lon_b) / 2.0


def line_length_m(line) -> float:
    """Length of the S/F line in metres (i.e. the width of the timed gate)."""
    (lat_a, lon_a), (lat_b, lon_b) = line
    lat0, lon0 = line_midpoint(line)
    x, y = _local_xy([lat_a, lat_b], [lon_a, lon_b], lat0, lon0)
    return float(math.hypot(x[1] - x[0], y[1] - y[0]))


def _side_and_position(t, lat, lon, line, tolerance_m: float):
    """Per-fix signed distance to the line, position along it, and path
    travelled - all in metres, in the line's local frame."""
    lat0, lon0 = line_midpoint(line)
    (lat_a, lon_a), (lat_b, lon_b) = line
    ends_x, ends_y = _local_xy([lat_a, lat_b], [lon_a, lon_b], lat0, lon0)
    ax, ay = ends_x[0], ends_y[0]
    dx, dy = ends_x[1] - ax, ends_y[1] - ay
    length = math.hypot(dx, dy)
    if length < 1e-6:
        raise ValueError("start/finish line endpoints coincide; give two distinct points")

    x, y = _local_xy(lat, lon, lat0, lon0)
    rel_x, rel_y = x - ax, y - ay
    # Signed perpendicular offset (which side of the line the kart is on) and
    # the distance along the line where it sits (must be within the gate).
    offset = (dx * rel_y - dy * rel_x) / length
    along = (dx * rel_x + dy * rel_y) / length
    path = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])

    side = np.sign(offset)
    side[np.abs(offset) <= tolerance_m] = 0.0
    return offset, along, path, side, length, x, y


# Window each side of a crossing over which the kart's NET displacement is
# measured. Long enough that GPS noise averages out (it is metre-scale and
# uncorrelated over a second), short enough to be one pass of the line.
SPEED_WINDOW_S = 1.0


def _net_speed(t, x, y, prev: int, cur: int) -> float:
    """Straight-line speed across the crossing, in m/s.

    NET displacement, not path length: a kart standing on the grid wanders
    metres per second in GPS terms but goes nowhere, and only distance
    actually covered means the line was passed.
    """
    lo = int(np.searchsorted(t, t[prev] - SPEED_WINDOW_S, side="left"))
    hi = int(np.searchsorted(t, t[cur] + SPEED_WINDOW_S, side="right")) - 1
    lo, hi = max(0, min(lo, prev)), min(len(t) - 1, max(hi, cur))
    span = t[hi] - t[lo]
    if span <= 0:
        return 0.0
    return float(math.hypot(x[hi] - x[lo], y[hi] - y[lo]) / span)


def crossings_by_line(
    t,
    lat,
    lon,
    line,
    tolerance_m: float = LINE_TOLERANCE_M,
    min_speed_ms: float = MIN_CROSSING_SPEED_MS,
    max_gap_s: float = MAX_FIX_GAP_S,
) -> list[float]:
    """Times (in SECONDS, like `t`) at which the GPS track crosses the S/F line.

    `t` must be in seconds - the filters below reason about speed, so a
    caller holding .xrk millisecond timecodes converts on the way in and
    back on the way out.

    `line` is ((lat_a, lon_a), (lat_b, lon_b)) - the two ends of the painted
    line. A crossing is a sign change of the kart's side of the line whose
    interpolated intersection falls WITHIN the segment (driving past the end
    of the line, e.g. through the pit exit, is not a crossing).

    Fixes are not evenly spaced (the logger records only while the kart
    moves, and GPS drops out), so every calculation here reads the fixes'
    own timecodes; nothing assumes a sample rate.

    Four filters keep only real lap boundaries:

      - the two fixes must be `max_gap_s` apart at most, so a hole in the
        log cannot be interpolated into a crossing that may never have
        happened;
      - the kart must be moving (`min_speed_ms`), so GPS jitter while it sits
        on the grid cannot fire the gate;
      - the crossing must be in the racing direction - the direction the
        majority of crossings go, so an out-of-session pit-lane pass the
        other way is ignored;
      - consecutive crossings must be at least half a lap of TRACK DISTANCE
        apart, never a time cutoff.

    The first surviving crossing is always kept: on a rolling/standing start
    the kart launches from behind the line (any grid slot) and crosses it
    within metres, and that crossing is exactly when the race starts.
    """
    t = np.asarray(t, dtype=float)
    offset, along, path, side, length, x, y = _side_and_position(
        t, lat, lon, line, tolerance_m
    )

    # Candidate crossings: every transition between the two sides, allowing
    # any number of "on the line" fixes in between.
    nonzero = np.nonzero(side)[0]
    cand: list[tuple[float, float, float]] = []  # (time, path, direction)
    for prev, cur in zip(nonzero, nonzero[1:]):
        if side[prev] == side[cur]:
            continue
        span = offset[prev] - offset[cur]
        if abs(span) < 1e-9:
            continue
        frac = offset[prev] / span
        if not (0.0 <= frac <= 1.0):
            continue
        at = along[prev] + frac * (along[cur] - along[prev])
        if not (-tolerance_m <= at <= length + tolerance_m):
            continue  # passed beyond the end of the line, not through it
        dt = t[cur] - t[prev]
        if not (0 < dt <= max_gap_s):
            continue  # no fixes, or a hole in them: nothing to interpolate
        if _net_speed(t, x, y, prev, cur) < min_speed_ms:
            continue  # stationary jitter, not a pass
        cand.append(
            (
                float(t[prev] + frac * dt),
                float(path[prev] + frac * (path[cur] - path[prev])),
                float(side[cur]),
            )
        )
    if not cand:
        return []

    # Racing direction = the way most crossings go.
    directions = [d for _, _, d in cand]
    racing = 1.0 if directions.count(1.0) >= directions.count(-1.0) else -1.0
    cand = [c for c in cand if c[2] == racing]
    if len(cand) < 2:
        return [c[0] for c in cand]

    lap_dist = float(np.median(np.diff([p for _, p, _ in cand])))
    kept: list[float] = [cand[0][0]]
    last = cand[0][1]
    for at_t, at_path, _ in cand[1:]:
        if at_path - last >= 0.5 * lap_dist:
            kept.append(at_t)
            last = at_path
    return kept


def laps_from_crossings(crossings, t_start: float, t_end: float) -> list[dict]:
    """Lap table [{num, start_time, end_time}] bounded by the recording.

    The out-lap (t_start -> first crossing) and in-lap (last crossing ->
    t_end) are included as the first/last entries; their outer bound is not a
    crossing, so telemetry.complete_laps/opened_laps treat them as out/in
    laps exactly as with the native .xrk beacon laps.
    """
    bounds = [float(t_start)] + [float(c) for c in crossings] + [float(t_end)]
    return [
        {"num": k, "start_time": bounds[k], "end_time": bounds[k + 1]}
        for k in range(len(bounds) - 1)
    ]


def sf_from_crossings(t, lat, lon, crossing_times) -> tuple[float, float]:
    """Recover the S/F coordinate: mean GPS lat/lon interpolated at each
    crossing time. Inverse of crossings_by_line - used to read the pinned S/F
    back out of a lap-boundary set."""
    t = np.asarray(t, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    la = [float(np.interp(c, t, lat)) for c in crossing_times]
    lo = [float(np.interp(c, t, lon)) for c in crossing_times]
    return float(np.mean(la)), float(np.mean(lo))
