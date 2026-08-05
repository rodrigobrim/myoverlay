"""Config loading: the [mychron] -> [telemetry] rename keeps old configs and
old download folders working."""

from pathlib import Path

from media_tools.config import load_config


def test_legacy_mychron_section_configures_telemetry(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(
        '[mychron]\ndata_dirs = ["D:/sessions"]\nauto_download = true\n', encoding="utf-8"
    )

    cfg = load_config(file)

    assert cfg.telemetry.data_dirs == [Path("D:/sessions")]
    assert cfg.telemetry.auto_download is True


def test_explicit_telemetry_section_wins_over_legacy(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(
        '[mychron]\ndata_dirs = ["D:/old"]\n\n[telemetry]\ndata_dirs = ["D:/new"]\n',
        encoding="utf-8",
    )

    cfg = load_config(file)

    assert cfg.telemetry.data_dirs == [Path("D:/new")]


def test_legacy_mychron_folder_is_still_scanned(cfg, tmp_path):
    """Installs made before the rename keep their sessions in <library>/mychron;
    they stay listed and ingestable without anything being moved."""
    downloads = tmp_path / "telemetry"
    downloads.mkdir()
    cfg.telemetry.data_dirs = [downloads]
    legacy = cfg.library_root / "mychron"
    legacy.mkdir(parents=True)

    # Appended, never first: new downloads still land in the configured dir.
    assert cfg.telemetry_dirs() == [downloads, legacy]


def test_absent_legacy_folder_is_not_listed(cfg):
    assert cfg.telemetry_dirs() == list(cfg.telemetry.data_dirs)
