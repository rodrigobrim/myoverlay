"""The render consuming the S/F-relapped derived file.

`telemetry._derived_laps` is how the overlay picks up corrected lap
boundaries: when a `<stem>.sf-relapped.parquet` sits next to the .xrk, its
embedded lap table (ms since log start) replaces the early beacon laps;
otherwise the pipeline falls back to the .xrk. These reproduce the checks run
by hand against the real `kgv e2_Race_a_0096.sf-relapped.parquet`.
"""

import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from media_tools.telemetry import _derived_laps


def write_derived(path, laps, extra_meta=None):
    """Write a minimal sf-relapped.parquet carrying `laps` (ms) in the
    Parquet schema metadata, the same shape sf_export.py produces."""
    meta = {b"laps": json.dumps(laps).encode()}
    if extra_meta:
        meta.update(extra_meta)
    table = pa.table({"channel": pa.array([], pa.string()),
                      "timecode_ms": pa.array([], pa.int64()),
                      "value": pa.array([], pa.float64())})
    pq.write_table(table.replace_schema_metadata(meta), path)


def test_derived_laps_read_ms_to_seconds_with_base(tmp_path):
    xrk = tmp_path / "kgv e2_Race_a_0096.xrk"
    xrk.write_bytes(b"")  # only the sibling name matters
    write_derived(
        tmp_path / "kgv e2_Race_a_0096.sf-relapped.parquet",
        [{"num": 0, "start_time": 0, "end_time": 64101},
         {"num": 1, "start_time": 64101, "end_time": 123261}],
    )
    laps = _derived_laps(xrk, base=0.0)
    assert laps == [(0, 0.0, 64.101), (1, 64.101, 123.261)]


def test_derived_laps_apply_session_base_offset(tmp_path):
    xrk = tmp_path / "s.xrk"
    xrk.write_bytes(b"")
    write_derived(tmp_path / "s.sf-relapped.parquet",
                  [{"num": 0, "start_time": 1000, "end_time": 61000}])
    # base offsets every boundary onto the day/session timeline (seconds).
    assert _derived_laps(xrk, base=100.0) == [(0, 101.0, 161.0)]


def test_derived_laps_absent_file_returns_none(tmp_path):
    xrk = tmp_path / "no_sidecar.xrk"
    xrk.write_bytes(b"")
    assert _derived_laps(xrk, base=0.0) is None


def test_derived_laps_file_without_laps_metadata_returns_none(tmp_path):
    xrk = tmp_path / "s.xrk"
    xrk.write_bytes(b"")
    write_derived(tmp_path / "s.sf-relapped.parquet", [], extra_meta={b"laps": b""})
    # empty/missing laps metadata -> fall back to the .xrk beacon laps
    assert _derived_laps(xrk, base=0.0) is None


def test_derived_laps_match_relap_pipeline(tmp_path):
    """End-to-end shape: crossings -> lap table -> derived file -> read back."""
    from media_tools.relap import laps_from_crossings

    laps_ms = laps_from_crossings([60_000, 120_000], t_start=0, t_end=180_000)
    xrk = tmp_path / "r.xrk"
    xrk.write_bytes(b"")
    write_derived(tmp_path / "r.sf-relapped.parquet", laps_ms)
    got = _derived_laps(xrk, base=0.0)
    assert got == [(0, 0.0, 60.0), (1, 60.0, 120.0), (2, 120.0, 180.0)]
