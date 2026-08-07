from pathlib import Path

import pytest

from media_tools.config import CameraConfig, Config, TelemetryConfig


@pytest.fixture(autouse=True)
def no_real_volumes(monkeypatch):
    """Tests must never scan (or ingest from!) the machine's real volumes/devices."""
    monkeypatch.setattr("media_tools.ingest.camera.find_dcim_sources", lambda: [])
    monkeypatch.setattr("media_tools.ingest.mtp.enumerate_mtp_videos", lambda extensions: ([], []))
    monkeypatch.setattr("media_tools.ingest.mtp.find_mtp_sources", lambda: [])


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        library_root=tmp_path / "library",
        camera=CameraConfig(source_dirs=[], timezone="America/Sao_Paulo"),
        telemetry=TelemetryConfig(data_dirs=[], timezone="America/Sao_Paulo"),
    )
