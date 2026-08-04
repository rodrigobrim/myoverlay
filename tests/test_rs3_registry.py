"""RS3 discovery via Windows' installed-programs record.

Windows registers RS3 under the Uninstall keys (what Add/Remove Programs and
Get-Package read), carrying DisplayVersion and InstallLocation. That is a
second, independent source for the version gate and - more usefully - tells
us where a non-default install actually lives.
"""

from media_tools.config import Config
from media_tools.ingest import rs3
from media_tools.ingest.rs3 import Rs3Install, normalise_version


def test_windows_records_four_part_versions():
    """RS3's window title says '3.83.39'; Windows says '3.83.39.0'. The gate
    compares against the title's form, so the record must be normalised."""
    assert normalise_version("3.83.39.0") == "3.83.39"
    assert normalise_version("3.83.39") == "3.83.39"
    assert normalise_version("  3.72.27.1  ") == "3.72.27"
    assert normalise_version(None) is None
    assert normalise_version("") is None


def test_registry_version_matches_the_validated_list_form():
    """A normalised registry version must satisfy the gate as-is - the whole
    point of normalising it."""
    cfg = Config()
    assert rs3.rs3_version_supported(normalise_version("3.83.39.0"), cfg)
    # ...and an unvalidated one still gets refused.
    assert not rs3.rs3_version_supported(normalise_version("3.84.0.0"), cfg)


def test_display_name_matching_tolerates_aim_spelling():
    """AiM writes 'RaceStudio 3' in one key and 'RaceStudio 3 3.83.39.0' in
    another; both must be recognised, unrelated software must not."""
    assert rs3._looks_like_rs3("RaceStudio 3")
    assert rs3._looks_like_rs3("RaceStudio 3 3.83.39.0")
    assert rs3._looks_like_rs3("Race Studio 3")
    assert not rs3._looks_like_rs3("Race Studio 2")
    assert not rs3._looks_like_rs3("RaceRender")


def test_install_location_is_searched_before_the_default_path(monkeypatch, tmp_path):
    """The reason to read the registry at all: an install somewhere other
    than C:/AIM_SPORT is still found."""
    odd = tmp_path / "D_Apps" / "RS3" / "64"
    odd.mkdir(parents=True)
    exe = odd / "AiMRS3-64-ReleaseU.exe"
    exe.write_bytes(b"")

    monkeypatch.setattr(rs3, "_RS3_EXE_CANDIDATES", ())
    monkeypatch.setattr(
        rs3,
        "rs3_registry_entries",
        lambda: [Rs3Install("RaceStudio 3", "3.83.39", str(tmp_path / "D_Apps" / "RS3"))],
    )
    assert rs3.find_rs3_exe(Config()) == exe


def test_configured_exe_path_still_wins(monkeypatch, tmp_path):
    """An explicit rs3.exe_path is the user's decision - the registry must
    never override it."""
    chosen = tmp_path / "chosen.exe"
    chosen.write_bytes(b"")
    monkeypatch.setattr(
        rs3,
        "rs3_registry_entries",
        lambda: [Rs3Install("RaceStudio 3", "3.83.39", str(tmp_path))],
    )
    cfg = Config()
    cfg.rs3.exe_path = chosen
    assert rs3.find_rs3_exe(cfg) == chosen


def test_version_falls_back_to_the_registry_when_no_exe(monkeypatch):
    """No exe found (or unreadable metadata) still yields a version, so the
    gate reports the real number instead of refusing an 'unknown' one."""
    monkeypatch.setattr(rs3, "find_rs3_exe", lambda cfg: None)
    monkeypatch.setattr(
        rs3,
        "rs3_registry_entries",
        lambda: [Rs3Install("RaceStudio 3", "3.83.39", None)],
    )
    assert rs3.rs3_installed_version(Config()) == "3.83.39"


def test_no_registry_entries_is_not_an_error(monkeypatch):
    monkeypatch.setattr(rs3, "find_rs3_exe", lambda cfg: None)
    monkeypatch.setattr(rs3, "rs3_registry_entries", list)
    assert rs3.rs3_installed_version(Config()) is None


def _detect_deps_js() -> str:
    from pathlib import Path

    return (
        Path(__file__).resolve().parents[1] / "packaging" / "msi" / "detect_deps.js"
    ).read_text(encoding="ascii")


def test_wizard_reads_the_same_registry_keys_as_the_cli():
    """Single source of truth: the wizard must look exactly where rs3.py
    looks. A wizard that admits an install the CLI then refuses to drive is
    the mismatch this shared source exists to prevent."""
    js = _detect_deps_js()
    for _hive, path in rs3._UNINSTALL_KEYS:
        assert path.replace("\\", "\\\\") in js, f"{path} missing from detect_deps.js"
    # ...and both consider the same hives.
    assert "0x80000002" in js  # HKLM
    assert "0x80000001" in js  # HKCU


def test_wizard_has_no_second_detection_path():
    """The MSI-product / folder-exists / exe-file-version fallbacks were
    removed on purpose - each was a way for the wizard to disagree with the
    CLI about which RS3 is installed."""
    js = _detect_deps_js()
    assert "Session.Installer" not in js
    assert "FolderExists" not in js
    assert "GetFileVersion" not in js


def test_cli_has_no_second_detection_path():
    """Same rule on the Python side: the exe's file metadata is no longer a
    version source."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "src" / "media_tools" / "ingest" / "rs3.py"
    ).read_text(encoding="utf-8")
    assert "GetFileVersionInfo" not in src


def test_wizard_reads_wmi_through_execmethod():
    """JScript cannot receive WMI [out] parameters from a direct
    reg.EnumKey(...) call - it gets the bare return code, so every hive
    looks empty and RS3 is reported missing on a machine that has it.
    ExecMethod + SpawnInstance_ is the only pattern that works, and the
    SAFEARRAY needs VBArray. Verified live with cscript."""
    js = _detect_deps_js()
    assert "ExecMethod" in js
    assert "SpawnInstance_" in js
    assert "VBArray" in js


def test_detect_deps_js_stays_pure_ascii():
    """The file promises ASCII (it is embedded in the MSI); non-ASCII would
    break the build that reads it with an ascii codec."""
    from pathlib import Path

    raw = (
        Path(__file__).resolve().parents[1] / "packaging" / "msi" / "detect_deps.js"
    ).read_bytes()
    raw.decode("ascii")


def test_both_sides_normalise_versions_the_same_way():
    """Windows stores four parts, the gate compares three."""
    js = _detect_deps_js()
    assert r"/(\d+)\.(\d+)\.(\d+)/" in js
    assert normalise_version("3.83.39.0") == "3.83.39"


def test_race_studio_2_is_not_mistaken_for_3():
    """Both sides must reject the sibling product; the JS regex needs the
    non-digit boundary so 'Race Studio 3' matches but 'Race Studio 2' does
    not, and 'RaceStudio 3 3.83.39.0' still does."""
    js = _detect_deps_js()
    assert r"race\s*studio\s*3(\D|$)" in js
    assert not rs3._looks_like_rs3("Race Studio 2")


def test_registry_read_never_raises():
    """Reading the live registry must be safe on any machine (including a
    non-Windows CI box, where winreg does not import)."""
    entries = rs3.rs3_registry_entries()
    assert isinstance(entries, list)
    for entry in entries:
        assert entry.version is None or normalise_version(entry.version) == entry.version
