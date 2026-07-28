from media_tools.ingest.rs3 import choose_dialog_answer


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
