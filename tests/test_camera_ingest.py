from datetime import date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from media_tools.ingest.camera import capture_time, ingest_camera
from media_tools.library import Library


def make_fake_card(tmp_path: Path) -> Path:
    dcim = tmp_path / "card" / "DCIM" / "100MEDIA"
    dcim.mkdir(parents=True)
    (dcim / "DJI_20260712141530_0001_D.MP4").write_bytes(b"video-one")
    (dcim / "DJI_20260712145010_0002_D.MP4").write_bytes(b"video-two-longer")
    (dcim / "DJI_20260712141530_0001_D.LRF").write_bytes(b"proxy")  # must be skipped
    # A clip shot just after local midnight belongs to the previous UTC day.
    (dcim / "DJI_20260713003001_0003_D.MP4").write_bytes(b"midnight")
    return tmp_path / "card" / "DCIM"


def test_capture_time_from_dji_filename(tmp_path):
    f = tmp_path / "DJI_20260712141530_0001_D.MP4"
    f.write_bytes(b"x")
    t = capture_time(f, ZoneInfo("America/Sao_Paulo"))
    assert t.tzinfo == timezone.utc
    # 14:15:30 in Sao Paulo (-03) == 17:15:30 UTC
    assert (t.hour, t.minute, t.second) == (17, 15, 30)


def test_capture_time_fallback_mtime(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    t = capture_time(f, ZoneInfo("UTC"))
    assert t.tzinfo == timezone.utc


def test_ingest_copies_new_files_and_is_idempotent(cfg, tmp_path):
    card = make_fake_card(tmp_path)

    report = ingest_camera(cfg, extra_sources=[card])
    assert len(report.copied) == 3
    assert not report.errors

    lib = Library(cfg.library_root)
    # Clips are grouped by camera-local capture date.
    m12 = lib.load_day(date(2026, 7, 12))
    m13 = lib.load_day(date(2026, 7, 13))
    assert [v.source_name for v in m12.videos] == [
        "DJI_20260712141530_0001_D.MP4",
        "DJI_20260712145010_0002_D.MP4",
    ]
    assert [v.source_name for v in m13.videos] == ["DJI_20260713003001_0003_D.MP4"]
    for v in m12.videos:
        assert (lib.day_dir(date(2026, 7, 12)) / v.file).is_file()

    # Second run: nothing new.
    report2 = ingest_camera(cfg, extra_sources=[card])
    assert report2.copied == []
    assert report2.skipped_known == 3
    assert len(Library(cfg.library_root).load_day(date(2026, 7, 12)).videos) == 2

    # Ingest must never delete or alter the originals on the card.
    originals = sorted(p.name for p in card.rglob("*") if p.is_file())
    assert originals == [
        "DJI_20260712141530_0001_D.LRF",
        "DJI_20260712141530_0001_D.MP4",
        "DJI_20260712145010_0002_D.MP4",
        "DJI_20260713003001_0003_D.MP4",
    ]
    assert (card / "100MEDIA" / "DJI_20260712141530_0001_D.MP4").read_bytes() == b"video-one"
