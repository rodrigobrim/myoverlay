"""Read-only Gate-1 scan: new-vs-known filtering, video<->telemetry
correlation, orphan-telemetry flagging, and that it writes nothing."""

import json
from datetime import date, datetime, timezone

import pytest

from media_tools.library import Library, VideoClip
from media_tools.scan import scan_new

UTC = timezone.utc


def _xrk(start, dur, laps, venue="kgv"):
    from media_tools.ingest.mychron import XrkInfo
    from media_tools.library import Lap

    return XrkInfo(
        start_utc=start, duration_s=dur,
        laps=[Lap(num=n, start_s=s, end_s=e) for n, s, e in laps],
        venue=venue, driver="", channels=["RPM"],
    )


@pytest.fixture
def scan_cfg(cfg, tmp_path):
    cam = tmp_path / "cam"
    cam.mkdir()
    tel = tmp_path / "tel"
    tel.mkdir()
    cfg.camera.source_dirs = [cam]
    cfg.mychron.rs3_data_dirs = [tel]
    return cfg, cam, tel


def test_scan_correlates_and_flags_orphan(scan_cfg, monkeypatch):
    cfg, cam, tel = scan_cfg
    # 22:14 Sao_Paulo == 01:14 UTC next day
    (cam / "DJI_20260716221400_0065_D.MP4").write_bytes(b"v" * 10)
    (cam / "DJI_20260716230000_0090_D.MP4").write_bytes(b"v" * 20)  # no telemetry
    (tel / "race.xrk").write_bytes(b"t" * 5)
    (tel / "orphan.xrk").write_bytes(b"t" * 6)

    monkeypatch.setattr("media_tools.ingest.camera.probe_duration_s", lambda p: 300.0)

    def fake_parse(path, tz):
        if path.name == "race.xrk":
            return _xrk(datetime(2026, 7, 17, 1, 14, tzinfo=UTC), 300.0,
                       [(0, 0, 10), (1, 10, 70), (2, 70, 129), (3, 129, 145)])
        return _xrk(datetime(2026, 7, 17, 5, 0, tzinfo=UTC), 120.0, [])

    monkeypatch.setattr("media_tools.ingest.mychron.parse_xrk", fake_parse)

    result = scan_new(cfg)

    assert len(result.video_groups) == 2
    by_name = {g.video.source_name: g for g in result.video_groups}
    race = by_name["DJI_20260716221400_0065_D.MP4"]
    assert [t.source_name for t in race.telemetry] == ["race.xrk"]
    assert race.telemetry[0].best_lap == "0:59.00"  # lap 70->129 = 59 s
    assert race.telemetry[0].lap_count == 4
    assert by_name["DJI_20260716230000_0090_D.MP4"].telemetry == []
    assert [t.source_name for t in result.orphan_telemetry] == ["orphan.xrk"]
    json.loads(result.model_dump_json())  # valid JSON for the GUI


def test_scan_skips_known_and_writes_nothing(scan_cfg, monkeypatch):
    cfg, cam, tel = scan_cfg
    monkeypatch.setattr("media_tools.ingest.camera.probe_duration_s", lambda p: 100.0)
    monkeypatch.setattr(
        "media_tools.ingest.mychron.parse_xrk",
        lambda p, tz: _xrk(datetime(2026, 7, 17, 1, 0, tzinfo=UTC), 60.0, []),
    )

    lib = Library(cfg.library_root)
    d = date(2026, 7, 17)
    known = cam / "DJI_20260716230000_0100_D.MP4"
    known.write_bytes(b"k" * 30)
    m = lib.load_day(d)
    m.videos.append(VideoClip(
        file="raw/video/x.MP4", source_name=known.name, size_bytes=30,
        start_utc_estimate=datetime(2026, 7, 17, 2, 0, tzinfo=UTC),
    ))
    lib.save_day(m)
    session_json = cfg.library_root / d.isoformat() / "session.json"
    before = session_json.read_bytes()

    (cam / "DJI_20260716221400_0065_D.MP4").write_bytes(b"v" * 10)  # a new one
    result = scan_new(cfg)

    names = {g.video.source_name for g in result.video_groups}
    assert known.name not in names  # already ingested -> skipped
    assert "DJI_20260716221400_0065_D.MP4" in names
    assert session_json.read_bytes() == before  # scan wrote nothing
