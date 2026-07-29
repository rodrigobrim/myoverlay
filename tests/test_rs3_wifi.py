"""WiFi-connect flow: device-row structure, and the ask-the-user branches.

The dialog's rows are owner-drawn (no text, no children), so these fakes
model exactly what UIA really exposes: rectangles and nothing else.
"""

import pytest

from media_tools.config import Config
from media_tools.ingest import rs3


class FakeRect:
    def __init__(self, top, height, left=100, width=900):
        self.top = top
        self.left = left
        self.right = left + width
        self.bottom = top + height

    def height(self):
        return self.bottom - self.top

    def width(self):
        return self.right - self.left


class FakeRow:
    def __init__(self, rect):
        self._rect = rect

    def rectangle(self):
        return self._rect

    def window_text(self):
        return ""  # owner-drawn: never any text


class FakeButton:
    def __init__(self, name, enabled=True):
        self._name = name
        self._enabled = enabled
        self.clicked = 0

    def window_text(self):
        return self._name

    def is_enabled(self):
        return self._enabled

    def click_input(self):
        self.clicked += 1


class FakeDialog:
    """Enough of the 'Available devices' dialog for the row logic."""

    def __init__(self, rows, buttons=()):
        self._rows = rows
        self._buttons = list(buttons)

    def window_text(self):
        return "Available devices"

    def descendants(self, control_type=None):
        if control_type == "ListItem":
            return self._rows
        if control_type == "Button":
            return self._buttons
        return []


# A real 3.83.39 listing: a 33px section header followed by 40px entries.
HEADER_H, ENTRY_H = 33, 40


def _dialog(entry_count):
    rows = [FakeRow(FakeRect(331, HEADER_H))]
    top = 331 + HEADER_H
    for _ in range(entry_count):
        rows.append(FakeRow(FakeRect(top, ENTRY_H)))
        top += ENTRY_H
    return FakeDialog(rows)


@pytest.fixture(autouse=True)
def _no_gui(monkeypatch):
    """Never touch the search box or OCR in unit tests."""
    monkeypatch.setattr(rs3, "_filter_search", lambda dlg, text: None)
    monkeypatch.setattr(rs3, "_row_label", lambda dlg, rect, i: f"AiM-DEV-{i}")
    monkeypatch.setattr(rs3.time, "sleep", lambda s: None)


def test_section_headers_are_not_offered_as_devices():
    """Headers ('AiM devices') are shorter than entries - that height
    difference is the only thing separating a label from a real device."""
    devices = rs3._scan_for_aim_devices(Config(), _dialog(2), [])
    assert [d.label for d in devices] == ["AiM-DEV-0", "AiM-DEV-1"]


def test_no_aim_devices_when_the_filtered_list_is_empty():
    assert rs3._scan_for_aim_devices(Config(), FakeDialog([]), []) == []


def test_rows_scrolled_out_of_view_are_ignored():
    """Rows outside the viewport report a zero-size rect; clicking one would
    click whatever is at (0,0) on the desktop."""
    rows = [FakeRow(FakeRect(331, HEADER_H)), FakeRow(FakeRect(364, ENTRY_H))]
    rows.append(FakeRow(FakeRect(0, 0, left=0, width=0)))
    devices = rs3._scan_for_aim_devices(Config(), FakeDialog(rows), [])
    assert len(devices) == 1


def test_a_single_device_is_chosen_without_asking(monkeypatch):
    """One MyChron: connect it. Asking would just be a pointless prompt."""
    asked = []
    chosen = _run_connect(monkeypatch, entry_count=1, ask=lambda q, o: asked.append(q))
    assert chosen == "MyChron6 Brim (WiFi)"
    assert asked == []


def test_several_devices_ask_which_one(monkeypatch):
    """Connecting drives someone's logger - with more than one in range the
    automation must never pick for the user."""
    questions = []

    def ask(question, options):
        questions.append((question, list(options)))
        return 1

    chosen = _run_connect(monkeypatch, entry_count=3, ask=ask)
    assert chosen == "MyChron6 Brim (WiFi)"
    assert len(questions) == 1
    question, options = questions[0]
    assert "which one" in question.lower()
    assert options == ["AiM-DEV-0", "AiM-DEV-1", "AiM-DEV-2"]
    # ...and the device it connected is the one that was picked.
    assert _connected_row_index(monkeypatch) == 1


def test_several_devices_without_a_console_refuse_to_guess(monkeypatch):
    """A scheduled run has nobody to ask: report and stop, never guess."""
    report = []
    chosen = _run_connect(monkeypatch, entry_count=2, ask=None, report=report)
    assert chosen is None
    assert any("2 AiM devices" in line for line in report)
    assert any("rerun interactively" in line for line in report)


def test_no_device_asks_the_user_to_connect_one(monkeypatch):
    """The 'none found' case: ask the user to turn the MyChron on, then rescan."""
    questions = []

    def ask(question, options):
        questions.append(question)
        return 1  # "Give up"

    report = []
    chosen = _run_connect(monkeypatch, entry_count=0, ask=ask, report=report)
    assert chosen is None
    assert questions and "turn it on" in questions[0].lower()
    assert any("turn the" in line.lower() for line in report)


def test_no_device_and_no_console_reports_plainly(monkeypatch):
    report = []
    chosen = _run_connect(monkeypatch, entry_count=0, ask=None, report=report)
    assert chosen is None
    assert any("none broadcasting WiFi" in line for line in report)


_clicked_row = {}


def _connected_row_index(monkeypatch):
    return _clicked_row.get("index")


def _run_connect(monkeypatch, entry_count, ask, report=None):
    """Drive _connect_over_wifi against a fake dialog and a fake main window."""
    dialog = _dialog(entry_count)
    report = [] if report is None else report
    _clicked_row.clear()

    monkeypatch.setattr(rs3, "_open_available_devices", lambda win, rep: dialog)
    monkeypatch.setattr(rs3, "_close_available_devices", lambda dlg, rep: None)

    def fake_click(dlg, device, rep):
        _clicked_row["index"] = device.index
        return True

    monkeypatch.setattr(rs3, "_click_row_and_connect", fake_click)
    monkeypatch.setattr(
        rs3,
        "_wait_for_connected_device",
        lambda win, timeout_s=0, report=None, settle_s=0: "MyChron6 Brim (WiFi)",
    )
    return rs3._connect_over_wifi(Config(), object(), report, None, ask)


def test_the_view_filter_and_settings_are_never_clicked(monkeypatch):
    """'View:' cycles all -> only mine -> ...: cycling it hides the device and
    pops an info dialog. 'Settings...' configures the device. A scan must
    touch neither, however the rest of the flow goes."""
    view = FakeButton("View: all")
    settings = FakeButton("Settings...")
    rows = [FakeRow(FakeRect(331, HEADER_H)), FakeRow(FakeRect(364, ENTRY_H))]
    dialog = FakeDialog(rows, [view, settings, FakeButton("Rescan")])

    devices = rs3._scan_for_aim_devices(Config(), dialog, [], rescan=True)

    assert len(devices) == 1
    assert view.clicked == 0
    assert settings.clicked == 0


def test_wifi_connect_can_be_turned_off():
    assert Config().rs3.wifi_connect is True
    cfg = Config()
    cfg.rs3.wifi_connect = False
    assert cfg.rs3.wifi_connect is False


def test_wlan_ssid_parser_skips_bssid(monkeypatch):
    """`AP BSSID :` also ends in SSID - only the exact SSID line counts, and
    the label is matched even though the rest of netsh output is localised."""
    import subprocess as sp

    netsh = (
        "    Estado                  : Conectado\n"
        "    SSID                   : Sissilandia\n"
        "    AP BSSID               : 78:8c:b5:a9:39:0f\n"
    )

    class R:
        stdout = netsh

    monkeypatch.setattr(rs3.subprocess, "run", lambda *a, **k: R())
    assert rs3._current_wlan_ssid() == "Sissilandia"


def test_wlan_ssid_none_when_disconnected(monkeypatch):
    class R:
        stdout = "    Estado                  : Desconectado\n"

    monkeypatch.setattr(rs3.subprocess, "run", lambda *a, **k: R())
    assert rs3._current_wlan_ssid() is None


def test_aim_prefix_is_the_search_filter():
    """The prefix both identifies AiM SSIDs and is what isolates them from
    house networks in the list."""
    assert Config().rs3.wifi_device_prefix == "AiM-"
