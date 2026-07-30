"""The raw NAV-SOL scanner that recovers true GPS UTC time from .xrk bytes.

The MyChron logger clock wanders (2047 dates, days-off dates), so session
dating trusts the GPS records embedded in the file. These tests build
synthetic byte streams shaped like the real records (validated against real
KGV .xrk files, where GPS week 2429 + iTOW reproduced the known
2047-10-29 -> 2026-07-13 clock-error correspondence).
"""

import struct
from datetime import datetime, timezone

from media_tools.gps_time import GPS_UTC_LEAP_S, gps_first_fix_utc


def _record(aim_tc: int, itow: int, week: int, fix: int = 3) -> bytes:
    """One NAV-SOL-shaped stretch: aim_tc | iTOW | fTOW | week | fix | pad."""
    return (
        struct.pack("<i", aim_tc)
        + struct.pack("<I", itow)
        + struct.pack("<i", 1234)  # fTOW within +/-500000
        + struct.pack("<H", week)
        + bytes([fix, 0x0C])
        + b"\x00" * 40  # rest of the NAV-SOL payload
    )


def _stream(records: list[bytes]) -> bytes:
    filler = b"\xa7" * 23  # odd length: exercises unaligned scanning
    return filler + filler.join(records) + filler


# GPS week 2429 begins Sunday 2026-07-26 00:00:00 GPS time.
WEEK = 2429
WEEK_START = datetime(2026, 7, 26, 0, 0, 0, tzinfo=timezone.utc)


def test_chained_records_yield_utc():
    # Tuesday 14:44:23 GPS time of that week, 25 Hz records.
    itow0 = (2 * 86400 + 14 * 3600 + 44 * 60 + 23) * 1000
    records = [_record(1_000_000 + i * 40, itow0 + i * 40, WEEK) for i in range(8)]
    result = gps_first_fix_utc(_stream(records))
    assert result is not None
    utc, aim_tc = result
    assert aim_tc == 1_000_000
    expected = WEEK_START.timestamp() + itow0 / 1000 - GPS_UTC_LEAP_S
    assert abs(utc.timestamp() - expected) < 0.001
    assert utc.year == 2026 and utc.month == 7 and utc.day == 28


def test_too_few_records_is_none():
    itow0 = 100_000_000
    records = [_record(50_000 + i * 40, itow0 + i * 40, WEEK) for i in range(3)]
    assert gps_first_fix_utc(_stream(records)) is None


def test_mismatched_deltas_are_rejected():
    # Plausible-looking records whose GPS deltas do NOT track the logger
    # timecode deltas - the chain invariant must reject them.
    itow0 = 100_000_000
    records = [_record(50_000 + i * 40, itow0 + i * 55, WEEK) for i in range(10)]
    assert gps_first_fix_utc(_stream(records)) is None


def test_random_bytes_are_none():
    import random

    rng = random.Random(42)
    data = bytes(rng.randrange(256) for _ in range(50_000))
    assert gps_first_fix_utc(data) is None


def test_chain_after_garbage_prefix_uses_first_chained_record():
    itow0 = 200_000_000
    bad = _record(10_000, 999, WEEK)  # isolated, chains with nothing
    good = [_record(90_000 + i * 40, itow0 + i * 40, WEEK) for i in range(7)]
    result = gps_first_fix_utc(_stream([bad] + good))
    assert result is not None
    _, aim_tc = result
    assert aim_tc == 90_000
