"""Launcher applies MSI wizard choices when creating config.toml."""

import importlib.util
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "myoverlay_launcher", REPO / "packaging" / "myoverlay_launcher.py"
)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def _make_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy2(REPO / "config.example.toml", repo / "config.example.toml")
    cfg = repo / "config.toml"
    shutil.copy2(repo / "config.example.toml", cfg)
    return repo, cfg


def test_language_and_resolution_seeded(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    launcher._apply_installer_settings(
        repo, cfg, {"language": "pt", "resolution": "fhd", "google_skipped": True}
    )
    text = cfg.read_text(encoding="utf-8")
    assert 'language = "pt"' in text
    assert 'resolution = "fhd"' in text
    assert 'language = "en"' not in text

    # The seeded config must still parse and validate.
    import tomllib

    from media_tools.config import Config

    parsed = Config.model_validate(tomllib.loads(text))
    assert parsed.language == "pt"
    assert parsed.render.resolution == "fhd"


def test_client_secret_copied(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    secret = tmp_path / "client_secret_download.json"
    secret.write_text('{"installed": {"client_id": "x.apps.googleusercontent.com"}}')
    launcher._apply_installer_settings(
        repo, cfg, {"language": "en", "client_secret": str(secret)}
    )
    assert json.loads((repo / "client_secret.json").read_text())["installed"]


def test_empty_settings_leave_defaults(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    before = cfg.read_text(encoding="utf-8")
    launcher._apply_installer_settings(repo, cfg, {})
    assert cfg.read_text(encoding="utf-8") == before
