"""Best lap is a single source of truth (telemetry.best_lap / valid_laps),
and the YouTube title resolves it over the S/F-relap-corrected laps - the same
laps the overlay renders - so title and video can never disagree.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from media_tools.library import DayManifest, Lap, TelemetryLog, TrackSession
from media_tools.telemetry import best_lap, valid_laps

# laps share boundaries so complete_laps sees real beacon crossings:
#   0: out-lap (start not a crossing)   1: 60s   2: 59s   3: 17s fragment
#   4: in-lap (end not a crossing)
LAPS = [
    (0, 0.0, 10.0),
    (1, 10.0, 70.0),
    (2, 70.0, 129.0),
    (3, 129.0, 146.0),
    (4, 146.0, 200.0),
]


def test_best_lap_picks_fastest_valid():
    assert best_lap(LAPS) == (2, 70.0, 129.0)  # 59 s, the fastest complete lap


def test_best_lap_excludes_out_in_and_fragment():
    valid = valid_laps(LAPS)
    nums = {n for n, _, _ in valid}
    assert nums == {1, 2}  # out-lap(0), fragment(3), in-lap(4) all dropped


def test_best_lap_respects_min_lap_s():
    # 59 s lap is now "impossible" (below the floor) -> the 60 s lap wins.
    assert best_lap(LAPS, min_lap_s=59.5) == (1, 10.0, 70.0)
    # a floor above every lap -> no best lap.
    assert best_lap(LAPS, min_lap_s=120.0) is None


def test_best_lap_empty():
    assert best_lap([]) is None
    assert valid_laps([]) == []


def _write_derived(path, laps_ms):
    table = pa.table({"channel": pa.array([], pa.string()),
                      "timecode_ms": pa.array([], pa.int64()),
                      "value": pa.array([], pa.float64())})
    meta = {b"laps": json.dumps(laps_ms).encode()}
    pq.write_table(table.replace_schema_metadata(meta), path)


def _one_session_manifest(day_dir):
    start = datetime(2026, 7, 16, 22, 14, tzinfo=timezone.utc)
    (day_dir / "raw" / "telemetry").mkdir(parents=True, exist_ok=True)
    # raw .xrk beacon laps -> fastest complete lap is 60 s
    raw = [Lap(num=0, start_s=0.0, end_s=10.0), Lap(num=1, start_s=10.0, end_s=70.0),
           Lap(num=2, start_s=70.0, end_s=130.0), Lap(num=3, start_s=130.0, end_s=145.0)]
    return DayManifest(
        date=date(2026, 7, 16), track="kgv",
        sessions=[TrackSession(id=1, start_utc=start, end_utc=start + timedelta(minutes=15),
                               telemetry_files=["raw/telemetry/s.xrk"])],
        telemetry=[TelemetryLog(file="raw/telemetry/s.xrk", source_name="s.xrk", size_bytes=1,
                                start_utc=start, end_utc=start + timedelta(minutes=15),
                                session_id=1, laps=raw)],
    )


def test_title_best_lap_is_derived_aware(cfg, tmp_path):
    """With a sibling .sf-relapped.parquet the title's best lap follows the
    CORRECTED laps, not the raw early-beacon .xrk laps."""
    from media_tools.publish import _title_context

    day_dir = tmp_path / "day"
    manifest = _one_session_manifest(day_dir)

    # without the sidecar: raw laps -> best complete lap is 60 s (1:00.00)
    assert _title_context(day_dir, manifest, 1).best_lap == "1:00.00"

    # corrected laps put the fastest complete lap at 59 s (0:59.00)
    _write_derived(
        day_dir / "raw" / "telemetry" / "s.sf-relapped.parquet",
        [{"num": 0, "start_time": 0, "end_time": 10000},
         {"num": 1, "start_time": 10000, "end_time": 70000},
         {"num": 2, "start_time": 70000, "end_time": 129000},
         {"num": 3, "start_time": 129000, "end_time": 145000}],
    )
    assert _title_context(day_dir, manifest, 1).best_lap == "0:59.00"
