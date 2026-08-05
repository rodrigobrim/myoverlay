import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

import media_tools.cli as cli
import media_tools.ingest.aim as aim
import media_tools.ingest.camera as camera
import media_tools.ingest.telemetry as tel_ingest
from media_tools.config import CameraConfig, Config, TelemetryConfig

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
        telemetry=TelemetryConfig(data_dirs=[], timezone="America/Sao_Paulo"),
    )
    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    return cfg


def test_video_list_remote_local_and_all(cfg_with_card):
    r = runner.invoke(cli.app, ["video", "list", "remote", "--json"])
    assert r.exit_code == 0
    data = json.loads(r.stdout)
    assert len(data["videos"]) == 2
    assert all(v["status"] == "new" for v in data["videos"])
    # Bare `video list` is the local view: nothing ingested yet.
    default = json.loads(runner.invoke(cli.app, ["video", "list", "--json"]).stdout)
    assert default["videos"] == []

    # Ingest one: remote hides it, default/`local` show it, `all` shows both.
    camera.ingest_camera(cfg_with_card, only_names=["DJI_20260712141530_0001_D.MP4"])
    remote = json.loads(runner.invoke(cli.app, ["video", "list", "remote", "--json"]).stdout)
    assert [v["source_name"] for v in remote["videos"]] == ["DJI_20260712145010_0002_D.MP4"]
    default = json.loads(runner.invoke(cli.app, ["video", "list", "--json"]).stdout)
    local = json.loads(runner.invoke(cli.app, ["video", "list", "local", "--json"]).stdout)
    assert default == local
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
        return tel_ingest.IngestReport()

    monkeypatch.setattr(tel_ingest, "ingest_telemetry", fake_ingest)
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
        return tel_ingest.IngestReport()

    monkeypatch.setattr(tel_ingest, "ingest_telemetry", fake_ingest)
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
    monkeypatch.setattr(tel_ingest, "ingest_telemetry", lambda cfg, **kw: tel_ingest.IngestReport())
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
    monkeypatch.setattr(tel_ingest, "ingest_telemetry", lambda cfg, **kw: tel_ingest.IngestReport())
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


def _library_with_video(cfg):
    from datetime import date, datetime, timedelta, timezone

    from media_tools.library import DayManifest, Library, TrackSession, VideoClip

    start = datetime(2026, 7, 12, 13, 0, tzinfo=timezone.utc)
    m = DayManifest(
        date=date(2026, 7, 12),
        sessions=[TrackSession(id=1, start_utc=start, end_utc=start + timedelta(minutes=20))],
    )
    m.videos = [
        VideoClip(
            file="raw/video/a.MP4", source_name="a.MP4", size_bytes=1,
            duration_s=60, start_utc_estimate=start, session_id=1,
        )
    ]
    Library(cfg.library_root).save_day(m)


def test_sync_video_alone_runs_auto_sync_on_that_clip(cfg_with_card, monkeypatch):
    import media_tools.sync as sync_mod

    _library_with_video(cfg_with_card)
    seen = {}

    def fake_sync_day(cfg, manifest, day_dir, force=False, only=None):
        seen["only"] = only
        seen["force"] = force
        return ["+ a.MP4: synced"]

    monkeypatch.setattr(sync_mod, "sync_day", fake_sync_day)
    r = runner.invoke(cli.app, ["sync", "2026-07-12", "--video", "a.MP4"])
    assert r.exit_code == 0
    assert seen["only"] == "a.MP4"


def test_sync_video_alone_requires_day_and_existing_clip(cfg_with_card):
    _library_with_video(cfg_with_card)
    r = runner.invoke(cli.app, ["sync", "--video", "a.MP4"])
    assert r.exit_code == 2
    r2 = runner.invoke(cli.app, ["sync", "2026-07-12", "--video", "MISSING.MP4"])
    assert r2.exit_code == 2
    assert "no video named" in r2.stdout


def test_sync_manual_mode_still_validates(cfg_with_card):
    _library_with_video(cfg_with_card)
    # An anchor option without the rest is still the manual-mode error.
    r = runner.invoke(cli.app, ["sync", "2026-07-12", "--video", "a.MP4", "--at", "00:30"])
    assert r.exit_code == 2
    assert "manual mode needs" in r.stdout


def test_ingest_force_threads_through(cfg_with_card, monkeypatch):
    seen = {}

    def fake_cam(cfg, **kw):
        seen["cam"] = kw
        return camera.IngestReport()

    def fake_myc(cfg, **kw):
        seen["myc"] = kw
        return tel_ingest.IngestReport()

    monkeypatch.setattr(camera, "ingest_camera", fake_cam)
    monkeypatch.setattr(tel_ingest, "ingest_telemetry", fake_myc)
    r = runner.invoke(cli.app, ["ingest", "--force"])
    assert r.exit_code == 0
    assert seen["cam"]["force"] is True
    assert seen["myc"]["force"] is True


def test_telemetry_list_defaults_to_local(cfg_with_card, tmp_path, monkeypatch):
    """Bare `telemetry list` is the local view: every .xrk on disk, ingested
    state included - and it never reaches for the device."""
    downloads = tmp_path / "telemetry"
    downloads.mkdir()
    (downloads / "a_0186.xrk").write_bytes(b"t" * 9)
    cfg_with_card.telemetry.data_dirs = [downloads]
    monkeypatch.setattr(
        tel_ingest, "parse_xrk", lambda p, tz: (_ for _ in ()).throw(ValueError("stub"))
    )
    monkeypatch.setattr(
        aim,
        "list_remote_sessions",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("device touched")),
    )

    default = json.loads(runner.invoke(cli.app, ["telemetry", "list", "--json"]).stdout)
    local = json.loads(runner.invoke(cli.app, ["telemetry", "list", "local", "--json"]).stdout)

    assert default == local
    assert [f["source_name"] for f in default["files"]] == ["a_0186.xrk"]
    assert default["files"][0]["ingested"] is False


def test_telemetry_list_local_shows_ingested_sessions(cfg_with_card, tmp_path, monkeypatch):
    """The reason for the listing: a session already ingested is still shown,
    flagged ingested, instead of vanishing from every local view."""
    from media_tools.library import DayManifest, Library, TelemetryLog

    day = cfg_with_card.library_root / "2026-07-30" / "raw" / "telemetry"
    day.mkdir(parents=True)
    (day / "a_0186.xrk").write_bytes(b"t" * 9)
    lib = Library(cfg_with_card.library_root)
    manifest = DayManifest(date=date(2026, 7, 30))
    manifest.telemetry.append(
        TelemetryLog(
            file="raw/telemetry/a_0186.xrk", source_name="a_0186.xrk", size_bytes=9,
            start_utc=datetime(2026, 7, 30, 22, 26, tzinfo=timezone.utc),
            end_utc=datetime(2026, 7, 30, 22, 46, tzinfo=timezone.utc),
        )
    )
    lib.save_day(manifest)
    monkeypatch.setattr(
        tel_ingest, "parse_xrk", lambda p, tz: (_ for _ in ()).throw(ValueError("stub"))
    )

    result = json.loads(runner.invoke(cli.app, ["telemetry", "list", "--json"]).stdout)

    assert [f["source_name"] for f in result["files"]] == ["a_0186.xrk"]
    assert result["files"][0]["ingested"] is True
    assert result["files"][0]["day"] == "2026-07-30"
