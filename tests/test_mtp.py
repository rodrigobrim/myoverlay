"""MTP (GoPro-style portable device) camera sources.

The PowerShell/COM layer itself only runs against real hardware; these tests
cover the JSON contract it emits and how MTP files flow through enumerate /
ingest / scan.
"""

import json
from datetime import date, datetime, timedelta, timezone

import media_tools.ingest.camera as camera
import media_tools.ingest.mtp as mtp
from media_tools.ingest.camera import enumerate_camera_videos, ingest_camera
from media_tools.library import Library
from media_tools.scan import scan_new

CREATED = datetime(2026, 8, 4, 18, 57, 14, tzinfo=timezone.utc)  # 15:57 in Sao Paulo


def gopro_file(name: str = "GH011045.MP4", size: int = 1024) -> mtp.MtpFile:
    return mtp.MtpFile(
        name=name,
        size=size,
        created_utc=CREATED,
        shell_path="::{20D04FE0-3AEA-1069-A2D8-08002B30309D}\\usb#vid_2672\\{0033}",
        display=f"HERO9 BLACK/DCIM/100GOPRO/{name}",
    )


def use_device(monkeypatch, files: list[mtp.MtpFile]):
    monkeypatch.setattr(
        "media_tools.ingest.mtp.enumerate_mtp_videos",
        lambda extensions: (["HERO9 BLACK/DCIM"], files),
    )


# --- PowerShell listing contract -------------------------------------------


def test_parse_listing():
    text = json.dumps(
        {
            "sources": ["HERO9 BLACK/DCIM"],
            "files": [
                {
                    "name": "GH011045.MP4",
                    "size": 216375870,
                    "created_utc": "2026-08-04T18:57:14Z",
                    "shell_path": "::{20D04FE0}\\x\\{0033}",
                    "display": "HERO9 BLACK/DCIM/100GOPRO/GH011045.MP4",
                }
            ],
        }
    )
    sources, files = mtp._parse_listing(text)
    assert sources == ["HERO9 BLACK/DCIM"]
    f = files[0]
    assert (f.name, f.size) == ("GH011045.MP4", 216375870)
    assert f.created_utc == CREATED
    assert f.display.endswith("GH011045.MP4")


def test_parse_listing_single_element_collapse():
    # PS 5.1 ConvertTo-Json collapses one-element collections to a scalar.
    text = json.dumps(
        {
            "sources": "HERO9 BLACK/DCIM",
            "files": {
                "name": "GH011045.MP4",
                "size": 7,
                "created_utc": "2026-08-04T18:57:14Z",
                "shell_path": "::{x}",
                "display": "HERO9 BLACK/DCIM/100GOPRO/GH011045.MP4",
            },
        }
    )
    sources, files = mtp._parse_listing(text)
    assert sources == ["HERO9 BLACK/DCIM"]
    assert len(files) == 1


def test_parse_listing_missing_date_falls_back_to_now():
    text = json.dumps(
        {
            "sources": ["X/DCIM"],
            "files": [
                {"name": "A.MP4", "size": 1, "created_utc": None, "shell_path": "::{x}", "display": "X/DCIM/A.MP4"}
            ],
        }
    )
    _sources, files = mtp._parse_listing(text)
    assert abs(files[0].created_utc - datetime.now(timezone.utc)) < timedelta(minutes=1)


def test_parse_listing_empty():
    assert mtp._parse_listing('{"sources":[],"files":[]}') == ([], [])


# --- enumerate / ingest / scan over MTP ------------------------------------


def test_enumerate_includes_mtp_videos(cfg, monkeypatch):
    use_device(monkeypatch, [gopro_file()])
    sources, files = enumerate_camera_videos(cfg)
    assert [str(s) for s in sources] == ["HERO9 BLACK\\DCIM"] or [str(s) for s in sources] == [
        "HERO9 BLACK/DCIM"
    ]
    (f,) = files
    # GoPro names carry no timestamp: the device recording date is used.
    assert f.start_utc == CREATED
    assert f.mtp is not None and not f.ingested


def test_enumerate_skips_appledouble_junk(cfg, monkeypatch):
    # A card that has visited a Mac carries "._GH....MP4" resource forks.
    use_device(monkeypatch, [gopro_file(), gopro_file(name="._GH011045.MP4", size=4)])
    _sources, files = enumerate_camera_videos(cfg)
    assert [f.name for f in files] == ["GH011045.MP4"]


def test_mtp_dji_filename_still_wins_over_device_date(cfg, monkeypatch):
    use_device(monkeypatch, [gopro_file(name="DJI_20260712141530_0001_D.MP4")])
    _sources, files = enumerate_camera_videos(cfg)
    # 14:15:30 Sao Paulo (-03) == 17:15:30 UTC, not the MTP created date.
    assert files[0].start_utc.hour == 17


def test_ingest_copies_mtp_file_and_is_idempotent(cfg, monkeypatch):
    f = gopro_file(size=9)
    use_device(monkeypatch, [f])
    copies = []

    def fake_copy(mf, dest_dir):
        copies.append(mf.name)
        (dest_dir / mf.name).write_bytes(b"x" * mf.size)

    monkeypatch.setattr("media_tools.ingest.mtp.copy_mtp_file", fake_copy)

    report = ingest_camera(cfg)
    assert copies == ["GH011045.MP4"] and not report.errors

    lib = Library(cfg.library_root)
    # Filed under the camera-local (Sao Paulo) capture day.
    manifest = lib.load_day(date(2026, 8, 4))
    (clip,) = manifest.videos
    assert clip.source_name == "GH011045.MP4" and clip.size_bytes == 9
    assert (lib.day_dir(date(2026, 8, 4)) / clip.file).is_file()

    report2 = ingest_camera(cfg)
    assert report2.copied == [] and report2.skipped_known == 1 and copies == ["GH011045.MP4"]


def test_ingest_records_mtp_copy_failure(cfg, monkeypatch):
    use_device(monkeypatch, [gopro_file()])
    monkeypatch.setattr(
        "media_tools.ingest.mtp.copy_mtp_file",
        lambda mf, dest_dir: (_ for _ in ()).throw(OSError("MTP copy failed: device unplugged")),
    )
    report = ingest_camera(cfg)
    assert report.copied == [] and len(report.errors) == 1
    assert "MTP copy failed" in report.errors[0]
    assert not Library(cfg.library_root).load_day(date(2026, 8, 4)).videos


def test_scan_new_lists_mtp_video_without_probing(cfg, monkeypatch):
    use_device(monkeypatch, [gopro_file()])
    monkeypatch.setattr(
        camera, "probe_duration_s", lambda p: (_ for _ in ()).throw(AssertionError("probed"))
    )
    result = scan_new(cfg)
    (group,) = result.video_groups
    assert group.video.source_name == "GH011045.MP4"
    assert group.video.duration_s is None and group.video.end_utc is None
