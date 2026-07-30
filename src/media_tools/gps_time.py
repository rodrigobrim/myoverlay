"""Recover the true UTC session time of an .xrk from its raw GPS records.

The MyChron's logger clock wanders (it has recorded 2047 dates, and dates two
days off after a reset), so the 'Log Date'/'Log Time' header is unreliable.
The GPS receiver, however, is authoritative: every u-blox NAV-SOL record in
the .xrk stream carries iTOW (GPS time-of-week, ms) and the GPS week number,
which together give absolute GPS time - libxrk parses these fields but
discards them.

Rather than reimplement libxrk's message demux, this module scans the raw
file bytes for NAV-SOL-shaped records and validates candidates with a strong
invariant: consecutive records must show the SAME week and GPS-time deltas
EQUAL to the AiM logger-timecode deltas that precede each record. Random
bytes essentially never satisfy that chain, so >= MIN_CHAIN chained records
is treated as proof.

Record layout as embedded in the stream (offsets relative to the iTOW field,
matching libxrk's _decode_gps 56-byte description):

    -4  int32   AiM logger timecode [ms]
     0  uint32  iTOW - GPS time of week [ms]
    +4  int32   fTOW - fractional TOW [ns], |x| <= 500000
    +8  uint16  GPS week number
    +10 uint8   gpsFix (0=none .. 5)
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
# GPS time is ahead of UTC by the leap seconds accumulated since 1980
# (18 s since 2017; the next leap second, if ever, can update this).
GPS_UTC_LEAP_S = 18

_WEEK_MIN, _WEEK_MAX = 1024, 4200  # ~1999..2060
_ITOW_WEEK_MS = 7 * 24 * 3600 * 1000
_MIN_CHAIN = 5  # consecutive validated record pairs required
_MAX_STEP_MS = 60_000  # max plausible gap between consecutive GPS records


def gps_first_fix_utc(data: bytes) -> tuple[datetime, int] | None:
    """(UTC of the first validated GPS record, its AiM logger timecode in ms),
    or None when the file carries no usable GPS time (e.g. indoors, no fix).

    The returned time is the file's FIRST GPS record, not the logging start:
    subtract the record's channel-relative timecode (e.g. the first 'GPS
    Latitude' timecode from a parsed log) to get the session start.
    """
    candidates = _scan_candidates(data)
    if len(candidates) < _MIN_CHAIN + 1:
        return None
    chained = _validate_chain(candidates)
    if chained is None:
        return None
    aim_tc, itow, week = chained
    utc = GPS_EPOCH + timedelta(
        weeks=week, milliseconds=itow, seconds=-GPS_UTC_LEAP_S
    )
    return utc, aim_tc


def gps_log_start_utc(path: Path) -> datetime | None:
    """Approximate UTC at which the .xrk's logging started, from raw bytes
    alone (no libxrk parse - usable even when the decoder rejects the file).
    Accuracy: the GPS receiver's first fix comes some seconds after power-on,
    so this is a lower-bound-ish estimate good for dating a session."""
    try:
        result = gps_first_fix_utc(path.read_bytes())
    except OSError:
        return None
    return result[0] if result else None


def _scan_candidates(data: bytes) -> list[tuple[int, int, int, int]]:
    """[(offset_of_iTOW, aim_tc, itow, week)] for every NAV-SOL-shaped spot.

    numpy prefilter on the week field keeps the python loop over a few percent
    of positions instead of every byte.
    """
    import numpy as np

    n = len(data)
    if n < 16:
        return []
    buf = np.frombuffer(data, dtype=np.uint8)
    out: list[tuple[int, int, int, int]] = []
    for parity in range(2):
        u2 = buf[parity : parity + (n - parity) // 2 * 2].view("<u2")
        for i in np.where((u2 >= _WEEK_MIN) & (u2 <= _WEEK_MAX))[0]:
            wp = parity + 2 * int(i)  # week field position
            h = wp - 8  # iTOW position
            if h < 4 or h + 12 > n:
                continue
            itow, ftow = struct.unpack_from("<Ii", data, h)
            if not 0 <= itow < _ITOW_WEEK_MS:
                continue
            if abs(ftow) > 500_000:
                continue
            if data[h + 10] > 5:  # gpsFix
                continue
            (aim_tc,) = struct.unpack_from("<i", data, h - 4)
            (week,) = struct.unpack_from("<H", data, wp)
            out.append((h, aim_tc, itow, week))
    out.sort()
    return out


def _validate_chain(
    candidates: list[tuple[int, int, int, int]],
) -> tuple[int, int, int] | None:
    """(aim_tc, itow, week) of the earliest record that opens a chain of
    >= _MIN_CHAIN consecutive candidates whose GPS-time deltas equal their
    logger-timecode deltas; None when no such chain exists."""
    chain_start = 0
    chain_len = 0
    for k in range(len(candidates) - 1):
        _, tc_a, itow_a, week_a = candidates[k]
        _, tc_b, itow_b, week_b = candidates[k + 1]
        step = itow_b - itow_a
        if week_a == week_b and step == tc_b - tc_a and 0 < step < _MAX_STEP_MS:
            if chain_len == 0:
                chain_start = k
            chain_len += 1
            if chain_len >= _MIN_CHAIN:
                _, aim_tc, itow, week = candidates[chain_start]
                return aim_tc, itow, week
        else:
            chain_len = 0
    return None
