from media_tools.config import Config
from media_tools.ingest.rs3 import (
    choose_dialog_answer,
    rs3_version_supported,
    version_from_title,
)


def test_plain_confirmation_is_confirmed():
    assert choose_dialog_answer(["Download completed", "4 sessions downloaded"]) == "confirm"
    assert choose_dialog_answer([]) == "confirm"


def test_data_removal_dialogs_are_declined():
    assert choose_dialog_answer(["Erase memory after download?"]) == "decline"
    assert choose_dialog_answer(["Delete downloaded sessions from the device?"]) == "decline"
    assert choose_dialog_answer(["Clear all data?"]) == "decline"


def test_hide_toggle_is_never_a_download_target():
    """'Hide Downloaded'/'Unhide Downloaded' contain 'download' but are
    display toggles - the forbidden list must cover both label states."""
    from media_tools.ingest.rs3 import _FORBIDDEN_WORDS

    for label in ("hide downloaded", "unhide downloaded"):
        assert any(w in label for w in _FORBIDDEN_WORDS)
    # ... while the real target stays clickable
    assert not any(w in "data download" for w in _FORBIDDEN_WORDS)


def test_share_upload_dialogs_are_declined():
    # RS3's "Upload to AiM?" nag: never send the user's data anywhere.
    assert choose_dialog_answer(["Upload to AiM server", "Flag the checkbox to share tracks with AiM"]) == "decline"
    assert choose_dialog_answer(["Upload automatically without asking"]) == "decline"


def test_device_configuration_dialogs_are_declined():
    """A download run must never reconfigure the MyChron: firmware update
    offers and WiFi-settings prompts get No/Cancel."""
    assert choose_dialog_answer(["A new firmware is available. Update now?"]) == "decline"
    assert choose_dialog_answer(["Executing firmup"]) == "decline"
    assert choose_dialog_answer(["Configure WiFi settings for MyChron6"]) == "decline"
    assert choose_dialog_answer(["Wi-Fi network changed. Apply?"]) == "decline"


def test_firmware_buttons_are_forbidden():
    from media_tools.ingest.rs3 import _FORBIDDEN_WORDS

    for label in ("firmware update", "start firmup"):
        assert any(w in label for w in _FORBIDDEN_WORDS)
    assert not any(w in "data download" for w in _FORBIDDEN_WORDS)


def test_wizard_and_cli_validate_the_same_rs3_versions():
    """The MSI wizard (detect_deps.js) and the runtime gate must agree on
    which RS3 versions are validated - one source drifting silently would
    let the wizard pass an install the automation then refuses to drive."""
    import json
    import re
    from pathlib import Path

    js = (
        Path(__file__).resolve().parents[1] / "packaging" / "msi" / "detect_deps.js"
    ).read_text(encoding="ascii")  # the file promises pure ASCII - hold it to that
    m = re.search(r"VALIDATED_RS3_VERSIONS\s*=\s*(\[[^\]]*\])", js)
    assert m, "VALIDATED_RS3_VERSIONS not found in detect_deps.js"
    assert json.loads(m.group(1)) == Config().rs3.validated_versions


def test_version_from_title():
    assert version_from_title("RaceStudio3 (64 bit) 3.83.39") == "3.83.39"
    assert version_from_title("RaceStudio3 (64 bit) 3.72.27") == "3.72.27"
    assert version_from_title("Race Studio 3") is None
    assert version_from_title("") is None
    assert version_from_title(None) is None


def test_only_snapshot_validated_rs3_versions_are_driven():
    cfg = Config()
    assert cfg.rs3.validated_versions == ["3.83.39"]
    assert rs3_version_supported("3.83.39", cfg)
    # Older/newer layouts we never snapshotted: refused, never clicked blind.
    assert not rs3_version_supported("3.72.27", cfg)
    assert not rs3_version_supported("3.83.11", cfg)
    assert not rs3_version_supported(None, cfg)
    # Explicitly emptying the list disables the gate.
    cfg.rs3.validated_versions = []
    assert rs3_version_supported("1.0.0", cfg)
