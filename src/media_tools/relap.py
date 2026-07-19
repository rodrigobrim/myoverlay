"""Start/finish re-lapping from GPS position.

The MyChron pins the S/F beacon deliberately early, so the .xrk lap table
fires ~seconds before the real start/finish. These helpers re-derive lap
boundaries purely from where the GPS track crosses a chosen S/F *coordinate*
- never from a clock. The standalone export uses them to write
`<stem>.sf-relapped.parquet`, which the render then consumes
(telemetry._derived_laps). The render pipeline never calls this module
directly; it only reads the derived file.
"""

from __future__ import annotations

import math

import numpy as np

DEFAULT_RADIUS_M = 12.0


def _local_xy(lat, lon, lat0: float, lon0: float):
    """Equirectangular metres east/north of (lat0, lon0) - exact enough at
    kart-track scale."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    x = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    y = (lat - lat0) * 111320.0
    return x, y


def crossings_by_coordinate(
    t, lat, lon, sf_lat: float, sf_lon: float, radius_m: float = DEFAULT_RADIUS_M
) -> list[float]:
    """Times (same unit as `t`) at which the GPS track crosses the S/F point.

    A crossing is the closest map approach while within `radius_m` of the
    point. A candidate is kept only once ~half a lap of track has been
    travelled since the previous kept crossing (measured from the start line
    at distance 0), so the grid-start proximity hit - the kart idling on the
    S/F straight - is excluded by DISTANCE TRAVELLED, not by any time cutoff.
    """
    t = np.asarray(t, dtype=float)
    x, y = _local_xy(lat, lon, sf_lat, sf_lon)
    dist = np.hypot(x, y)
    path = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])

    cand: list[int] = []
    i, n = 0, len(t)
    while i < n:
        if dist[i] <= radius_m:
            j = i
            while j < n and dist[j] <= radius_m:
                j += 1
            cand.append(i + int(np.argmin(dist[i:j])))  # closest approach
            i = j
        else:
            i += 1
    if not cand:
        return []

    lap_dist = float(np.median(np.diff(path[cand]))) if len(cand) > 1 else float(path[-1])
    kept: list[float] = []
    last = 0.0
    for k in cand:
        if path[k] - last >= 0.5 * lap_dist:
            kept.append(float(t[k]))
            last = path[k]
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
    crossing time. Inverse of crossings_by_coordinate - used to read the
    pinned S/F back out of a lap-boundary set."""
    t = np.asarray(t, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    la = [float(np.interp(c, t, lat)) for c in crossing_times]
    lo = [float(np.interp(c, t, lon)) for c in crossing_times]
    return float(np.mean(la)), float(np.mean(lo))
