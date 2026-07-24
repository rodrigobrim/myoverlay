import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import media_tools.cli as cli
import media_tools.ingest.camera as camera
import media_tools.ingest.mychron as mychron
import media_tools.ingest.rs3 as rs3
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
        mychron=MychronConfig(rs3_data_dirs=[], timezone="America/Sao_Paulo"),
    )
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    return cfg


def test_camera_list_default_and_all(cfg_with_card):
    r = runner.invoke(cli.app, ["camera", "list", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert len(data["videos"]) == 2
    assert all(v["status"] == "new" for v in data["videos"])

    # Ingest one, then default listing hides it but --all shows it.
    camera.ingest_camera(cfg_with_card, only_names=["DJI_20260712141530_0001_D.MP4"])
    default = json.loads(runner.invoke(cli.app, ["camera", "list", "--json"]).stdout)
    assert [v["source_name"] for v in default["videos"]] == ["DJI_20260712145010_0002_D.MP4"]
    everything = json.loads(runner.invoke(cli.app, ["camera", "list", "--all", "--json"]).stdout)
    assert len(everything["videos"]) == 2


def test_camera_get_all_then_missing(cfg_with_card):
    r = runner.invoke(cli.app, ["camera", "get"])
    assert r.exit_code == 0

    r2 = runner.invoke(cli.app, ["camera", "get", "MISSING.MP4"])
    assert r2.exit_code == 1
    assert "not found on camera" in r2.stdout


def test_telemetry_get_drives_rs3_and_ingests(cfg_with_card, monkeypatch):
    calls = {"rs3": 0}
    monkeypatch.setattr(rs3, "trigger_rs3_download", lambda cfg, troubleshoot=False: (calls.__setitem__("rs3", calls["rs3"] + 1) or ["ok"]))

    seen = {}

    def fake_ingest(cfg, **kw):
        seen["kw"] = kw
        return mychron.IngestReport()

    monkeypatch.setattr(mychron, "ingest_mychron", fake_ingest)
    # A telemetry download must never touch camera ingest.
    monkeypatch.setattr(camera, "ingest_camera", lambda *a, **k: (_ for _ in ()).throw(AssertionError("camera touched")))

    r = runner.invoke(cli.app, ["telemetry", "get"])
    assert r.exit_code == 0
    assert calls["rs3"] == 1
    assert seen["kw"] == {"only_names": None, "force": False}


def test_telemetry_get_no_rs3_skips_pull(cfg_with_card, monkeypatch):
    monkeypatch.setattr(rs3, "trigger_rs3_download", lambda cfg, troubleshoot=False: (_ for _ in ()).throw(AssertionError("rs3 driven")))
    monkeypatch.setattr(mychron, "ingest_mychron", lambda cfg, **kw: mychron.IngestReport())
    r = runner.invoke(cli.app, ["telemetry", "get", "--no-rs3"])
    assert r.exit_code == 0


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
