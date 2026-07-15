from media_tools.ingest.rs3 import choose_dialog_answer


def test_plain_confirmation_is_confirmed():
    assert choose_dialog_answer(["Download completed", "4 sessions downloaded"]) == "confirm"
    assert choose_dialog_answer([]) == "confirm"


def test_data_removal_dialogs_are_declined():
    assert choose_dialog_answer(["Erase memory after download?"]) == "decline"
    assert choose_dialog_answer(["Delete downloaded sessions from the device?"]) == "decline"
    assert choose_dialog_answer(["Clear all data?"]) == "decline"
