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


def _install_dir_value(text: str) -> str:
    import tomllib

    from media_tools.config import Config

    parsed = Config.model_validate(tomllib.loads(text))
    return str(parsed.tools.install_dir)


def test_install_dir_inserted_under_existing_tools_section(tmp_path):
    # config.example.toml already ships a [tools] section (commented body).
    repo, cfg = _make_repo(tmp_path)
    launcher._upsert_install_dir(cfg, "C:/Apps/MyOverlay")
    text = cfg.read_text(encoding="utf-8")
    assert 'install_dir = "C:/Apps/MyOverlay"' in text
    assert _install_dir_value(text) == "C:\\Apps\\MyOverlay"
    # Other sections survive.
    assert "[youtube]" in text and "[render]" in text


def test_install_dir_appended_when_no_tools_section(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = repo / "config.toml"
    cfg.write_text('library_root = "D:/lib"\nlanguage = "en"\n', encoding="utf-8")
    launcher._upsert_install_dir(cfg, "D:/Games/MyOverlay")
    text = cfg.read_text(encoding="utf-8")
    assert "[tools]" in text
    assert _install_dir_value(text) == "D:\\Games\\MyOverlay"


def test_install_dir_refreshed_when_stale(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    launcher._upsert_install_dir(cfg, "C:/old/MyOverlay")
    launcher._upsert_install_dir(cfg, "C:/new/MyOverlay")
    text = cfg.read_text(encoding="utf-8")
    assert "C:/old/MyOverlay" not in text
    assert _install_dir_value(text) == "C:\\new\\MyOverlay"


def test_install_dir_noop_when_already_current(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    launcher._upsert_install_dir(cfg, "C:/Apps/MyOverlay")
    once = cfg.read_text(encoding="utf-8")
    launcher._upsert_install_dir(cfg, "C:/Apps/MyOverlay")
    assert cfg.read_text(encoding="utf-8") == once


def test_install_dir_backslashes_normalized(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    launcher._upsert_install_dir(cfg, "C:\\Program Files\\MyOverlay")
    text = cfg.read_text(encoding="utf-8")
    assert 'install_dir = "C:/Program Files/MyOverlay"' in text
    # Still valid TOML.
    _install_dir_value(text)


def test_parse_settings_yaml_flat_map(tmp_path):
    text = (
        "# myoverlay install settings\n"
        "language: pt\n"
        "resolution: fhd\n"
        "client_secret: C:\\Users\\me\\client_secret.json\n"
        "google_skipped: false\n"
        'install_dir: "C:/Program Files/MyOverlay"\n'
        "\n"
    )
    settings = launcher._parse_settings_yaml(text)
    assert settings["language"] == "pt"
    assert settings["resolution"] == "fhd"
    # Windows path keeps its drive-letter colon (split on first colon only).
    assert settings["client_secret"] == "C:\\Users\\me\\client_secret.json"
    assert settings["install_dir"] == "C:/Program Files/MyOverlay"
    # true/false coerce to bool to match the old JSON semantics.
    assert settings["google_skipped"] is False
