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
    # The repo root's config.toml is the shipped template (all options
    # commented out); the launcher copies it as the user's config.
    cfg = repo / "config.toml"
    shutil.copy2(REPO / "config.toml", cfg)
    return repo, cfg


def test_language_and_resolution_seeded(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    launcher._apply_installer_settings(
        cfg, {"language": "pt", "resolution": "fhd", "google_skipped": True}
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
        cfg, {"language": "en", "client_secret": str(secret)}
    )
    assert json.loads((cfg.parent / "client_secret.json").read_text())["installed"]


def test_empty_settings_leave_defaults(tmp_path):
    repo, cfg = _make_repo(tmp_path)
    before = cfg.read_text(encoding="utf-8")
    launcher._apply_installer_settings(cfg, {})
    assert cfg.read_text(encoding="utf-8") == before


def _install_dir_value(text: str) -> str:
    import tomllib

    from media_tools.config import Config

    parsed = Config.model_validate(tomllib.loads(text))
    return str(parsed.tools.install_dir)


def test_install_dir_inserted_under_existing_tools_section(tmp_path):
    # The template already ships a [tools] section (commented body).
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
    launcher._upsert_install_dir(cfg, "D:\\Custom Apps\\MyOverlay")
    text = cfg.read_text(encoding="utf-8")
    assert 'install_dir = "D:/Custom Apps/MyOverlay"' in text
    # Still valid TOML.
    _install_dir_value(text)


def test_install_dir_default_location_never_written(tmp_path):
    # The default install dir lives in the code (ToolsConfig); config.toml
    # only carries deviations, so a stock install leaves the line commented.
    repo, cfg = _make_repo(tmp_path)
    before = cfg.read_text(encoding="utf-8")
    launcher._upsert_install_dir(cfg, "C:\\Program Files\\MyOverlay")
    assert cfg.read_text(encoding="utf-8") == before
    assert _install_dir_value(before) == "C:\\Program Files\\MyOverlay"


def test_template_is_all_defaults(tmp_path):
    # The shipped template must be valid TOML whose parse equals a fully
    # default Config: every option present but commented out.
    import tomllib

    from media_tools.config import Config

    text = (REPO / "config.toml").read_bytes().decode("utf-8-sig")
    parsed = Config.model_validate(tomllib.loads(text))
    assert parsed == Config()


def test_wizard_resolution_combo_single_sourced():
    # resolutions.json is the single source of truth for the resolution
    # presets: config.RESOLUTIONS must be exactly its parse, and the wizard's
    # ComboBox must take its items from the build-time generated include
    # (build_msi.ps1), never hardcode them in the .wxs.
    from media_tools.config import RESOLUTIONS

    src = json.loads(
        (REPO / "src" / "media_tools" / "resolutions.json").read_text(encoding="utf-8")
    )
    assert src == RESOLUTIONS

    wxs = (REPO / "packaging" / "msi" / "WizardUI.wxs").read_text(encoding="utf-8")
    assert "<?include resolutions.wxi ?>" in wxs
    for name in RESOLUTIONS:
        assert f'ListItem Text="{name} (' not in wxs

    ps1 = (REPO / "packaging" / "msi" / "build_msi.ps1").read_text(encoding="utf-8")
    assert "resolutions.json" in ps1 and "resolutions.wxi" in ps1

    # The config.toml template's comment enumerates the presets for humans;
    # keep it in step with the source of truth.
    template = (REPO / "config.toml").read_bytes().decode("utf-8-sig")
    line = next(
        ln for ln in template.splitlines() if "Output resolution preset" in ln
    )
    for name, height in RESOLUTIONS.items():
        assert f"{name} ({height}p)" in line


def test_parse_settings_yaml_flat_map(tmp_path):
    text = (
        "# MyOverlay install settings\n"
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
