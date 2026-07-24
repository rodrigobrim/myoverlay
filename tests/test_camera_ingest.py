from datetime import date, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import media_tools.ingest.camera as camera
from media_tools.ingest.camera import capture_time, enumerate_camera_videos, ingest_camera
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


def test_enumerate_marks_ingested(cfg, tmp_path):
    card = make_fake_card(tmp_path)
    _sources, files = enumerate_camera_videos(cfg, extra_sources=[card])
    # 3 real clips, .LRF proxy excluded, none ingested yet.
    assert {f.name for f in files} == {
        "DJI_20260712141530_0001_D.MP4",
        "DJI_20260712145010_0002_D.MP4",
        "DJI_20260713003001_0003_D.MP4",
    }
    assert all(not f.ingested for f in files)

    ingest_camera(cfg, extra_sources=[card])
    _sources2, files2 = enumerate_camera_videos(cfg, extra_sources=[card])
    assert all(f.ingested for f in files2)


def test_enumerate_does_not_probe_duration(cfg, tmp_path, monkeypatch):
    card = make_fake_card(tmp_path)
    monkeypatch.setattr(
        camera, "probe_duration_s", lambda p: (_ for _ in ()).throw(AssertionError("probed"))
    )
    _sources, files = enumerate_camera_videos(cfg, extra_sources=[card])
    assert len(files) == 3


def test_only_names_selective_download(cfg, tmp_path):
    card = make_fake_card(tmp_path)
    report = ingest_camera(cfg, extra_sources=[card], only_names=["DJI_20260712145010_0002_D.MP4"])
    assert report.copied and len(report.copied) == 1
    assert report.requested_missing == []

    lib = Library(cfg.library_root)
    m12 = lib.load_day(date(2026, 7, 12))
    assert [v.source_name for v in m12.videos] == ["DJI_20260712145010_0002_D.MP4"]
    # The other clips were not touched.
    assert not lib.day_dir(date(2026, 7, 13)).joinpath("session.json").exists() or not lib.load_day(
        date(2026, 7, 13)
    ).videos


def test_only_names_is_case_insensitive(cfg, tmp_path):
    card = make_fake_card(tmp_path)
    report = ingest_camera(cfg, extra_sources=[card], only_names=["dji_20260712141530_0001_d.mp4"])
    assert len(report.copied) == 1
    assert report.requested_missing == []


def test_only_names_missing_is_reported(cfg, tmp_path):
    card = make_fake_card(tmp_path)
    report = ingest_camera(cfg, extra_sources=[card], only_names=["NOPE.MP4"])
    assert report.copied == []
    assert report.requested_missing == ["NOPE.MP4"]
    lib = Library(cfg.library_root)
    assert not lib.day_dir(date(2026, 7, 12)).joinpath("session.json").exists()


def test_force_replaces_entry_in_place(cfg, tmp_path):
    card = make_fake_card(tmp_path)
    name = "DJI_20260712141530_0001_D.MP4"

    ingest_camera(cfg, extra_sources=[card], only_names=[name])
    # Without --force a second run skips it.
    again = ingest_camera(cfg, extra_sources=[card], only_names=[name])
    assert again.copied == [] and again.skipped_known == 1

    forced = ingest_camera(cfg, extra_sources=[card], only_names=[name], force=True)
    assert len(forced.copied) == 1 and forced.skipped_known == 0

    # Manifest still has exactly one clip for that source (replaced, not duplicated).
    lib = Library(cfg.library_root)
    m12 = lib.load_day(date(2026, 7, 12))
    assert [v.source_name for v in m12.videos].count(name) == 1
