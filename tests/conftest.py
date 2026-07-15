from pathlib import Path

import pytest

from media_tools.config import CameraConfig, Config, MychronConfig


@pytest.fixture(autouse=True)
def no_real_volumes(monkeypatch):
    """Tests must never scan (or ingest from!) the machine's real volumes."""
    monkeypatch.setattr("media_tools.ingest.camera.find_dcim_sources", lambda: [])


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        library_root=tmp_path / "library",
        camera=CameraConfig(source_dirs=[], timezone="America/Sao_Paulo"),
        mychron=MychronConfig(rs3_data_dirs=[], timezone="America/Sao_Paulo"),
    )
