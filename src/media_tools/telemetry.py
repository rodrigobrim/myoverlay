"""Unified telemetry frame built from MyChron .xrk files.

Produces a pandas DataFrame with a canonical set of columns regardless of the
logger's channel naming:

    t_s        seconds since log start (float)
    rpm        engine RPM
    speed_ms   speed in m/s (normalized from the channel's unit)
    lat, lon   GPS position (degrees)
    alt        GPS altitude (m)
    water_temp coolant temperature (logger unit, typically C)

plus a GPX export used by the overlay renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Canonical column -> candidate channel names, first match wins.
CHANNEL_ALIASES: dict[str, list[str]] = {
    "rpm": ["Engine RPM", "RPM", "Engine Speed", "Engine"],
    "jackshaft": ["Jackshaft"],  # driveline rpm on karts; rpm proxy when the plug lead is absent
    "speed_ms": ["GPS Speed", "Vehicle Speed", "Speed"],
    "lat": ["GPS Latitude"],
    "lon": ["GPS Longitude"],
    "alt": ["GPS Altitude"],
    # MyChron 6 names its probe inputs Temp TR/TC (thermoresistance/thermocouple).
    "water_temp": ["Water Temp", "WaterTemp", "Water Temperature", "Temp Water", "Temp TR 1", "WaterT"],
    "exhaust_temp": ["Exhaust Temp", "EGT", "Exhaust Temperature", "Temp TC 1"],
    # GPS-derived accelerations are already in the kart frame, in g.
    "g_lat": ["GPS_LateralAcc", "GPS LateralAcc", "Lateral Acc"],
    "g_lon": ["GPS_InlineAcc", "GPS InlineAcc", "Inline Acc", "Longitudinal Acc"],
    "steering_deg": ["Steering Angle", "Steering Pos", "Steering"],
}

# Units that mean the raw value must be scaled to m/s.
_SPEED_SCALES = {
    "km/h": 1 / 3.6,
    "kmh": 1 / 3.6,
    "kph": 1 / 3.6,
    "mph": 0.44704,
    "m/s": 1.0,
    "mps": 1.0,
    "kt": 0.514444,
}


def _channel_unit(log, name: str) -> str | None:
    field = log.channels[name].schema.field(name)
    meta = field.metadata or {}
    for key in (b"units", b"unit"):
        if key in meta:
            return meta[key].decode("utf-8", "replace").strip()
    return None


def _pick_channel(log, candidates: list[str]) -> str | None:
    for name in candidates:
        if name in log.channels:
            return name
    # Fall back to a case-insensitive substring match so minor naming
    # variations across firmware versions still resolve.
    lowered = {n.lower(): n for n in log.channels}
    for name in candidates:
        for low, actual in lowered.items():
            if name.lower() in low:
                return actual
    return None


def unified_frame(log) -> pd.DataFrame:
    """Build the canonical DataFrame from a libxrk LogFile."""
    mapping: dict[str, str] = {}
    for canonical, candidates in CHANNEL_ALIASES.items():
        actual = _pick_channel(log, candidates)
        if actual:
            mapping[canonical] = actual

    if not mapping:
        raise ValueError(f"{log.file_name}: no recognizable channels ({sorted(log.channels)})")

    selected = log.select_channels(sorted(set(mapping.values())))
    table = selected.get_channels_as_table().to_pandas()

    df = pd.DataFrame({"t_s": table["timecodes"] / 1000.0})
    for canonical, actual in mapping.items():
        df[canonical] = table[actual]

    if "speed_ms" in df.columns:
        unit = (_channel_unit(log, mapping["speed_ms"]) or "km/h").lower()
        scale = _SPEED_SCALES.get(unit, 1 / 3.6)  # AiM defaults to km/h
        df["speed_ms"] = df["speed_ms"] * scale

    # AiM's GPS_LateralAcc and the MyChron steering sensor both read POSITIVE in
    # a LEFT turn (driver frame), but the overlay's convention is positive =
    # right. Flip them once here so the wheel graphic and the lateral G dot draw
    # the real direction. Verified against footage: two clear left-hand corners
    # logged +g_lat and +steering while the kart turned left. (Longitudinal
    # g_lon is already correct: braking-down / accel-up.)
    for col in ("g_lat", "steering_deg"):
        if col in df.columns:
            df[col] = -df[col]

    # Drop rows before GPS fix (lat/lon exactly 0 is the no-fix filler).
    if {"lat", "lon"} <= set(df.columns):
        no_fix = (df["lat"] == 0.0) & (df["lon"] == 0.0)
        df = df[~no_fix].reset_index(drop=True)

    # Drop channels that carry no data: unconnected sensors log constant
    # zeros (RPM lead absent) or +inf (unplugged temp probes). Keeping them
    # would freeze widgets at fake values and break channel fallbacks.
    import numpy as np

    for col in list(df.columns):
        if col in ("t_s", "lat", "lon"):
            continue
        v = df[col].to_numpy(dtype=float)
        finite = v[np.isfinite(v)]
        if len(finite) == 0 or finite.std() < 1e-9:
            df = df.drop(columns=[col])

    return df


def load_unified(xrk_path: Path) -> pd.DataFrame:
    from libxrk import aim_xrk

    return unified_frame(aim_xrk(str(xrk_path)))


@dataclass
class DayFrame:
    """All of a day's telemetry on one timeline (t_s from first log start).

    Gaps between stints are filled with zero speed/rpm (the kart is standing
    in the pits) and NaN GPS, so envelope-based sync sees the on/off pattern
    and the overlay shows 0 km/h rather than interpolated garbage.
    """

    df: pd.DataFrame
    start_utc: datetime
    laps: list[tuple[int, float, float]]  # (lap_num, start_s, end_s)


ENGINE_LIKE_COLUMNS = ("speed_ms", "rpm", "jackshaft")


def load_day_frame(day_dir: Path, manifest) -> DayFrame:
    import numpy as np

    logs = [t for t in manifest.telemetry if t.start_utc]
    if not logs:
        raise ValueError("day has no telemetry")
    logs = sorted(logs, key=lambda t: t.start_utc)
    start_utc = logs[0].start_utc

    parts: list[pd.DataFrame] = []
    laps: list[tuple[int, float, float]] = []
    prev_end_s: float | None = None
    for log in logs:
        df = load_unified(day_dir / log.file).copy()
        base = (log.start_utc - start_utc).total_seconds()
        df["t_s"] = df["t_s"] + base
        gap_start = prev_end_s
        if gap_start is not None and float(df["t_s"].iloc[0]) - gap_start > 2.0:
            gap_t = np.arange(gap_start + 1.0, float(df["t_s"].iloc[0]) - 0.5, 1.0)
            gap = pd.DataFrame({"t_s": gap_t})
            for col in df.columns:
                if col == "t_s":
                    continue
                gap[col] = 0.0 if col in ENGINE_LIKE_COLUMNS else np.nan
            parts.append(gap)
        parts.append(df)
        prev_end_s = float(df["t_s"].iloc[-1])
        for lap in log.laps:
            laps.append((lap.num, base + lap.start_s, base + lap.end_s))

    merged = pd.concat(parts, ignore_index=True).sort_values("t_s").reset_index(drop=True)
    laps.sort(key=lambda x: x[1])
    return DayFrame(df=merged, start_utc=start_utc, laps=laps)


def load_session_frame(day_dir: Path, manifest, session) -> pd.DataFrame:
    """Concatenate all telemetry logs of a session into one frame.

    t_s is re-based to seconds since session.start_utc so multiple .xrk files
    covering one stint line up on a single timeline.
    """
    parts = []
    for log in manifest.telemetry:
        if log.session_id != session.id or not log.start_utc:
            continue
        df = load_unified(day_dir / log.file)
        df = df.copy()
        df["t_s"] = df["t_s"] + (log.start_utc - session.start_utc).total_seconds()
        parts.append(df)
    if not parts:
        raise ValueError(f"session {session.id} has no telemetry frames")
    out = pd.concat(parts, ignore_index=True).sort_values("t_s").reset_index(drop=True)
    return out


def session_laps(manifest, session) -> list[tuple[int, float, float]]:
    """(lap_num, start_s, end_s) relative to session.start_utc, in order."""
    laps = []
    for log in manifest.telemetry:
        if log.session_id != session.id or not log.start_utc:
            continue
        base = (log.start_utc - session.start_utc).total_seconds()
        for lap in log.laps:
            laps.append((lap.num, base + lap.start_s, base + lap.end_s))
    laps.sort(key=lambda x: x[1])
    return laps


def complete_laps(
    laps: list[tuple[int, float, float]], eps: float = 0.05
) -> list[tuple[int, float, float]]:
    """Laps the MyChron opened AND closed with a beacon crossing.

    A crossing simultaneously ends one lap and starts the next, so a boundary
    time that appears as BOTH some lap's end and some lap's start is a real
    crossing. The out-lap's start (recording power-on) matches no lap's end,
    and the in-lap's end (power-off) matches no lap's start - both are dropped.
    Only complete laps are eligible as a best lap or a delta reference.
    """
    starts = [s for _, s, _ in laps]
    ends = [e for _, _, e in laps]
    out = []
    for n, st, e in laps:
        opened = any(abs(en - st) <= eps for en in ends)
        closed = any(abs(s - e) <= eps for s in starts)
        if opened and closed:
            out.append((n, st, e))
    return out


def opened_laps(
    laps: list[tuple[int, float, float]], eps: float = 0.05
) -> list[tuple[int, float, float]]:
    """Laps the MyChron opened with a beacon crossing (their start is a
    crossing - i.e. some other lap ends there).

    Includes a lap still in progress and the in-lap (opened but truncated by
    power-off); excludes the out-lap, whose start is the recording power-on,
    not a crossing. Used for the *current* lap timer, which must count only a
    real lap in progress - never the out-lap.
    """
    ends = [e for _, _, e in laps]
    return [
        (n, st, e) for n, st, e in laps if any(abs(en - st) <= eps for en in ends)
    ]


def export_gpx(df: pd.DataFrame, start_utc: datetime, dest: Path) -> Path:
    """Write the GPS track as GPX 1.1 for the overlay renderer.

    Point times are absolute UTC (start_utc + t_s), which is what lets the
    renderer align telemetry with the synced video timeline.
    """
    import gpxpy.gpx as gpx_mod

    if not {"lat", "lon"} <= set(df.columns):
        raise ValueError("telemetry frame has no GPS channels; cannot export GPX")

    gpx = gpx_mod.GPX()
    track = gpx_mod.GPXTrack()
    segment = gpx_mod.GPXTrackSegment()
    track.segments.append(segment)
    gpx.tracks.append(track)

    has_alt = "alt" in df.columns
    for row in df.itertuples(index=False):
        point = gpx_mod.GPXTrackPoint(
            latitude=float(row.lat),
            longitude=float(row.lon),
            elevation=float(row.alt) if has_alt else None,
            time=start_utc + timedelta(seconds=float(row.t_s)),
        )
        segment.points.append(point)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(gpx.to_xml(), encoding="utf-8")
    return dest
