import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import media_tools.cli as cli
import media_tools.ingest.aim as aim
import media_tools.ingest.camera as camera
import media_tools.ingest.mychron as mychron
from media_tools.config import CameraConfig, Config, MychronConfig

runner = CliRunner()


def _card(tmp_path: Path) -> Path:
    dcim = tmp_path / "card" / "DCIM" / "100MEDIA"
    dcim.mkdir(parents=True)
    (dcim / "DJI_20260712141530_0001_D.MP4").write_bytes(b"one")
    (dcim / "DJI_20260712145010_0002_D.MP4").write_bytes(b"two-longer")
    return tmp_path / "card" / "DCIM"


@pytest.fixture
def cfg_with_card(tmp_path, monkeypatch):
    card = _card(tmp_path)
    cfg = Config(
        library_root=tmp_path / "library",
        camera=CameraConfig(source_dirs=[card], timezone="America/Sao_Paulo"),
        mychron=MychronConfig(data_dirs=[], timezone="America/Sao_Paulo"),
    )
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    return cfg


def test_video_list_remote_local_and_all(cfg_with_card):
    r = runner.invoke(cli.app, ["video", "list", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert len(data["videos"]) == 2
    assert all(v["status"] == "new" for v in data["videos"])
    # Bare `video list` and `video list remote` are the same view.
    remote = json.loads(runner.invoke(cli.app, ["video", "list", "remote", "--json"]).stdout)
    assert remote == data

    # Ingest one: default/remote hides it, `local` shows it, `all` shows both.
    camera.ingest_camera(cfg_with_card, only_names=["DJI_20260712141530_0001_D.MP4"])
    default = json.loads(runner.invoke(cli.app, ["video", "list", "--json"]).stdout)
    assert [v["source_name"] for v in default["videos"]] == ["DJI_20260712145010_0002_D.MP4"]
    local = json.loads(runner.invoke(cli.app, ["video", "list", "local", "--json"]).stdout)
    assert [v["source_name"] for v in local["videos"]] == ["DJI_20260712141530_0001_D.MP4"]
    assert local["videos"][0]["day"] == "2026-07-12"
    everything = json.loads(runner.invoke(cli.app, ["video", "list", "all", "--json"]).stdout)
    assert len(everything["remote"]["videos"]) == 2
    assert len(everything["local"]["videos"]) == 1


def test_video_get_all_then_missing(cfg_with_card):
    r = runner.invoke(cli.app, ["video", "get"])
    assert r.exit_code == 0

    r2 = runner.invoke(cli.app, ["video", "get", "MISSING.MP4"])
    assert r2.exit_code == 1
    assert "not found on camera" in r2.stdout


def test_video_get_by_day(cfg_with_card):
    # A day arg expands to every file captured that day.
    r = runner.invoke(cli.app, ["video", "get", "2026-07-12"])
    assert r.exit_code == 0
    from media_tools.library import Library

    lib = Library(cfg_with_card.library_root)
    assert len(lib.known_videos()) == 2

    # A day with nothing on the card is an explicit error, not a silent no-op.
    r2 = runner.invoke(cli.app, ["video", "get", "1999-01-01"])
    assert r2.exit_code == 1
    assert "no videos on the camera for" in r2.stdout


def test_telemetry_get_downloads_from_device_and_ingests(cfg_with_card, monkeypatch):
    calls = {"download": 0}

    def fake_download(cfg, names=None, force=False, echo=None, progress=None):
        calls["download"] += 1
        assert names is None
        return aim.DownloadReport()

    monkeypatch.setattr(aim, "download_sessions", fake_download)

    seen = {}

    def fake_ingest(cfg, **kw):
        seen["kw"] = kw
        return mychron.IngestReport()

    monkeypatch.setattr(mychron, "ingest_mychron", fake_ingest)
    # A telemetry download must never touch camera ingest.
    monkeypatch.setattr(camera, "ingest_camera", lambda *a, **k: (_ for _ in ()).throw(AssertionError("camera touched")))

    r = runner.invoke(cli.app, ["telemetry", "get"])
    assert r.exit_code == 0
    assert calls["download"] == 1
    assert seen["kw"] == {"only_names": None, "force": False}


def test_telemetry_get_named_sessions_ingest_their_downloads(cfg_with_card, monkeypatch, tmp_path):
    monkeypatch.setattr(
        aim,
        "download_sessions",
        lambda cfg, names=None, force=False, echo=None, progress=None: aim.DownloadReport(
            downloaded=[str(tmp_path / "a_0186.xrk")]
        ),
    )
    seen = {}

    def fake_ingest(cfg, **kw):
        seen["kw"] = kw
        return mychron.IngestReport()

    monkeypatch.setattr(mychron, "ingest_mychron", fake_ingest)
    r = runner.invoke(cli.app, ["telemetry", "get", "a_0186.xrz"])
    assert r.exit_code == 0
    # The device name maps to the .xrk the download produced.
    assert seen["kw"] == {"only_names": ["a_0186.xrk"], "force": False}


def test_telemetry_get_no_download_skips_device(cfg_with_card, monkeypatch):
    monkeypatch.setattr(
        aim,
        "download_sessions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("device driven")),
    )
    monkeypatch.setattr(mychron, "ingest_mychron", lambda cfg, **kw: mychron.IngestReport())
    r = runner.invoke(cli.app, ["telemetry", "get", "--no-download"])
    assert r.exit_code == 0


def test_telemetry_get_device_error_fails(cfg_with_card, monkeypatch):
    monkeypatch.setattr(
        aim,
        "download_sessions",
        lambda cfg, names=None, force=False, echo=None, progress=None: aim.DownloadReport(
            errors=["No MyChron found."]
        ),
    )
    monkeypatch.setattr(mychron, "ingest_mychron", lambda cfg, **kw: mychron.IngestReport())
    r = runner.invoke(cli.app, ["telemetry", "get"])
    assert r.exit_code == 1


def test_remote_listing_formatters():
    # Real values from a MyChron6 catalog row (day-first dates, ms lap times).
    assert cli._fmt_device_dt({"date": "30/07/2026", "hour": "22:26:00"}) == "2026-07-30 22:26:00"
    assert cli._fmt_device_dt({"date": "30/07/2026"}) == "2026-07-30 00:00:00"
    assert cli._fmt_device_dt({"date": "garbled", "hour": "x"}) == "garbled x"
    assert cli._fmt_device_dt({}) == "?"
    assert cli._fmt_lap_ms("52509") == "52.509"
    assert cli._fmt_lap_ms("62345") == "1:02.345"
    assert cli._fmt_lap_ms("") == "?"
    assert cli._fmt_lap_ms(None) == "?"
    assert cli._fmt_lap_ms("0") == "?"


def test_ingest_force_threads_through(cfg_with_card, monkeypatch):
    seen = {}

    def fake_cam(cfg, **kw):
        seen["cam"] = kw
        return camera.IngestReport()

    def fake_myc(cfg, **kw):
        seen["myc"] = kw
        return mychron.IngestReport()

    monkeypatch.setattr(camera, "ingest_camera", fake_cam)
    monkeypatch.setattr(mychron, "ingest_mychron", fake_myc)
    r = runner.invoke(cli.app, ["ingest", "--force"])
    assert r.exit_code == 0
    assert seen["cam"]["force"] is True
    assert seen["myc"]["force"] is True
