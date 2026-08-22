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


def test_kgv_tracks_ship_built_in(tmp_path):
    """An untouched config already knows KGV: its start/finish line resolves
    for any GPS position near Granja Viana."""
    file = tmp_path / "config.toml"
    file.write_text("", encoding="utf-8")

    cfg = load_config(file)

    line = cfg.tracks.start_finish_for_position(-23.6045, -46.8365)
    assert line == ((-23.604910, -46.836278), (-23.604930, -46.836352))
    found = cfg.tracks.layout_for_position(-23.6045, -46.8365)
    assert (found[0], found[1]) == ("kgv", "default")


def test_configured_tracks_join_the_built_in_one(tmp_path):
    """Adding a circuit does not drop KGV, and the session's own GPS decides
    which track applies - never a per-day setting."""
    file = tmp_path / "config.toml"
    file.write_text(
        "[tracks.beto-carrero.layouts.full.start-finish]\n"
        "coordinate1 = [-26.803000, -48.611000]\n"
        "coordinate2 = [-26.803050, -48.611080]\n"
        "[tracks.beto-carrero.layouts.full.box-entry]\n"
        "coordinate1 = [-26.803500, -48.610500]\n"
        "coordinate2 = [-26.803550, -48.610580]\n",
        encoding="utf-8",
    )

    cfg = load_config(file)

    # Near the new circuit -> its layout, with the box line along for later.
    found = cfg.tracks.layout_for_position(-26.8032, -48.6112)
    assert (found[0], found[1]) == ("beto-carrero", "full")
    assert found[2].box_entry.line()[0] == (-26.803500, -48.610500)
    # Near Granja Viana -> still the built-in KGV.
    assert cfg.tracks.layout_for_position(-23.6045, -46.8365)[0] == "kgv"


def test_layouts_of_one_track_resolve_by_their_own_lines(tmp_path):
    """Two layouts of the same circuit with different S/F positions: the one
    whose line is nearest the driven GPS wins."""
    file = tmp_path / "config.toml"
    file.write_text(
        "[tracks.t.layouts.short.start-finish]\n"
        "coordinate1 = [-20.000000, -46.000000]\n"
        "coordinate2 = [-20.000050, -46.000080]\n"
        "[tracks.t.layouts.long.start-finish]\n"
        "coordinate1 = [-20.003000, -46.000000]\n"
        "coordinate2 = [-20.003050, -46.000080]\n",
        encoding="utf-8",
    )

    cfg = load_config(file)

    assert cfg.tracks.layout_for_position(-20.0001, -46.0000)[1] == "short"
    assert cfg.tracks.layout_for_position(-20.0029, -46.0000)[1] == "long"


def test_coordinate_must_be_lat_lon_pair(tmp_path):
    import pytest

    file = tmp_path / "config.toml"
    file.write_text(
        "[tracks.t.layouts.a.start-finish]\n"
        "coordinate1 = [-20.0]\n"
        "coordinate2 = [-20.1, -46.1]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(file)
