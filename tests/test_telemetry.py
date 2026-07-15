from datetime import datetime, timezone

import pyarrow as pa
import pytest

from media_tools.telemetry import export_gpx, unified_frame


def make_channel(name: str, timecodes_ms, values, unit: str | None = None, interpolate=True):
    fields = [
        pa.field("timecodes", pa.int64()),
        pa.field(
            name,
            pa.float64(),
            metadata={
                b"units": (unit or "").encode(),
                b"interpolate": b"True" if interpolate else b"False",
                b"dec_pts": b"2",
            },
        ),
    ]
    return pa.table(
        {"timecodes": pa.array(timecodes_ms, type=pa.int64()), name: pa.array(values, type=pa.float64())},
        schema=pa.schema(fields),
    )


def make_log(channels: dict, laps=None):
    from libxrk import LogFile

    laps_table = pa.table(
        laps
        or {
            "num": pa.array([], type=pa.int64()),
            "start_time": pa.array([], type=pa.int64()),
            "end_time": pa.array([], type=pa.int64()),
        }
    )
    return LogFile(channels=channels, laps=laps_table, metadata={}, file_name="test.xrk")


def make_typical_log():
    tc = [0, 100, 200, 300]
    return make_log(
        {
            "Engine RPM": make_channel("Engine RPM", tc, [8000, 9000, 10000, 11000], "rpm"),
            "GPS Speed": make_channel("GPS Speed", tc, [36.0, 54.0, 72.0, 90.0], "km/h"),
            "GPS Latitude": make_channel("GPS Latitude", tc, [0.0, -23.7011, -23.7012, -23.7013], "deg"),
            "GPS Longitude": make_channel("GPS Longitude", tc, [0.0, -46.6971, -46.6972, -46.6973], "deg"),
            "Water Temp": make_channel("Water Temp", tc, [55.0, 55.5, 56.0, 56.5], "C"),
        }
    )


def test_unified_frame_maps_and_normalizes():
    df = unified_frame(make_typical_log())
    # First row (no GPS fix: lat==lon==0) must be dropped.
    assert len(df) == 3
    assert df["t_s"].tolist() == [0.1, 0.2, 0.3]
    # km/h -> m/s
    assert df["speed_ms"].tolist() == pytest.approx([15.0, 20.0, 25.0])
    assert df["rpm"].tolist() == [9000, 10000, 11000]
    assert df["water_temp"].iloc[0] == 55.5


def test_unified_frame_rejects_unknown_channels():
    log = make_log({"Mystery": make_channel("Mystery", [0], [1.0])})
    with pytest.raises(ValueError, match="no recognizable channels"):
        unified_frame(log)


def test_export_gpx(tmp_path):
    df = unified_frame(make_typical_log())
    start = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    dest = export_gpx(df, start, tmp_path / "out" / "track.gpx")
    text = dest.read_text(encoding="utf-8")
    assert text.count("<trkpt") == 3
    assert 'lat="-23.7011"' in text
    # absolute UTC point times: 13:00:00.100 for t_s=0.1
    assert "13:00:00" in text
