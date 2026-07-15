from datetime import date, datetime, timezone

from media_tools.library import DayManifest, Library, VideoClip


def make_clip(name="DJI_20260712101500_0001_D.MP4", size=1000):
    return VideoClip(
        file=f"raw/video/{name}",
        source_name=name,
        size_bytes=size,
        start_utc_estimate=datetime(2026, 7, 12, 13, 15, tzinfo=timezone.utc),
    )


def test_manifest_roundtrip(tmp_path):
    lib = Library(tmp_path / "library")
    m = DayManifest(date=date(2026, 7, 12), track="Interlagos")
    m.videos.append(make_clip())
    lib.save_day(m)

    loaded = lib.load_day(date(2026, 7, 12))
    assert loaded.track == "Interlagos"
    assert loaded.videos[0].source_name == "DJI_20260712101500_0001_D.MP4"
    assert loaded.videos[0].start_utc_estimate.tzinfo is not None
    assert lib.day_dates() == [date(2026, 7, 12)]


def test_known_videos_across_days(tmp_path):
    lib = Library(tmp_path / "library")
    m1 = DayManifest(date=date(2026, 7, 12))
    m1.videos.append(make_clip("a.MP4", 10))
    lib.save_day(m1)
    m2 = DayManifest(date=date(2026, 7, 13))
    m2.videos.append(make_clip("b.MP4", 20))
    lib.save_day(m2)

    assert lib.known_videos() == {("a.MP4", 10), ("b.MP4", 20)}


def test_load_day_tolerates_utf8_bom(tmp_path):
    """A manifest hand-edited on Windows may carry a BOM; it must still load."""
    lib = Library(tmp_path / "library")
    m = DayManifest(date=date(2026, 7, 12), track="KGV 101")
    lib.save_day(m)
    path = lib.day_dir(date(2026, 7, 12)) / "session.json"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert lib.load_day(date(2026, 7, 12)).track == "KGV 101"


def test_load_missing_day_returns_empty(tmp_path):
    lib = Library(tmp_path / "library")
    m = lib.load_day(date(2026, 1, 1))
    assert m.videos == [] and m.telemetry == []
