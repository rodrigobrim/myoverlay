"""Best-effort Race Studio 3 GUI automation.

RS3 has no CLI and the MyChron WiFi protocol is proprietary, so the only
zero-touch path for the device->PC hop is driving RS3's own UI. This module
launches RS3 if needed and clicks the first enabled download control it can
find. Everything is defensive: any failure is reported as text, never raised,
because the rest of the pipeline works fine from files a human downloaded.

One-time RS3 setup that makes this effective:
  Preferences -> Data Download: enable automatic CSV export on download.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from ..config import Config


class _Troubleshoot:
    """Diagnostics harness for the RS3 flow.

    Saves a numbered screenshot and a UIA control-tree dump at each step into
    <library_root>/rs3_troubleshoot/ so the procedure can be understood and
    refined. Every capture is best-effort - diagnostics must never break the
    run they are observing.
    """

    def __init__(self, cfg: Config) -> None:
        self.dir = Path(cfg.library_root) / "rs3_troubleshoot"
        self.dir.mkdir(parents=True, exist_ok=True)
        # Start clean so a fresh run's snapshots are unambiguous.
        for old in self.dir.glob("*"):
            try:
                if old.is_file():
                    old.unlink()
            except OSError:
                pass
        self.n = 0

    def snap(self, window, name: str) -> None:
        self.n += 1
        try:
            img = window.capture_as_image()
            if img is not None:
                img.save(str(self.dir / f"{self.n:02d}_{name}.png"))
        except Exception:  # noqa: BLE001 - a snapshot must never break the run
            pass

    def snap_desktop(self, name: str) -> None:
        self.n += 1
        try:
            from PIL import ImageGrab

            ImageGrab.grab().save(str(self.dir / f"{self.n:02d}_{name}.png"))
        except Exception:  # noqa: BLE001
            pass

    def dump(self, window, name: str) -> None:
        """Write each control's type/name/enabled/rect - the map needed to
        target the right button when a screenshot is ambiguous.

        A bare window.descendants() came back empty on this RS3 UIA tree, so
        iterate the control types the flow actually uses; that reliably
        enumerates them.
        """
        types = [
            "Button", "TabItem", "List", "ListItem", "CheckBox",
            "Text", "Pane", "Custom", "Hyperlink", "MenuItem", "Group",
        ]
        lines: list[str] = []
        seen: set[tuple] = set()
        for ct in types:
            try:
                controls = window.descendants(control_type=ct)
            except Exception:  # noqa: BLE001
                continue
            for c in controls:
                try:
                    rect = tuple(c.rectangle())
                    key = (ct, c.window_text(), rect)
                    if key in seen:
                        continue
                    seen.add(key)
                    lines.append(
                        f"{ct}\t{(c.window_text() or '')!r}\t"
                        f"enabled={c.is_enabled()}\t{rect}"
                    )
                except Exception:  # noqa: BLE001 - a vanished control is fine
                    continue
        try:
            (self.dir / f"{self.n:02d}_{name}.txt").write_text(
                "\n".join(lines) or "(no controls captured)", encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


def trigger_rs3_download(cfg: Config, troubleshoot: bool = False) -> list[str]:
    """Drive RS3 to download from the MyChron. Retries once: RS3 crashes and
    UIA/COM hiccups (busy repaint, app restart) are transient in practice.

    troubleshoot=True runs a single, heavily-instrumented pass (snapshots +
    control dumps at every step, unhides already-downloaded sessions, scrolls
    the list) with short 30s waits, to understand/refine the procedure.
    """
    if not cfg.rs3.enabled:
        return ["rs3 automation disabled (set [rs3] enabled = true)"]

    report: list[str] = []
    ts = _Troubleshoot(cfg) if troubleshoot else None
    # Troubleshooting is a single deliberate pass; don't blur it with a retry.
    attempts = (1,) if ts else (1, 2)
    for attempt in attempts:
        try:
            _attempt_download(cfg, report, ts)
            if ts:
                report.append(f"troubleshoot snapshots -> {ts.dir}")
            return report
        except Exception as exc:  # noqa: BLE001 - never kill the pipeline
            if ts:
                ts.snap_desktop("error")
                report.append(f"! rs3 troubleshoot pass raised: {exc!r}")
                report.append(f"troubleshoot snapshots -> {ts.dir}")
                return report
            if attempt == 1:
                report.append(f"~ rs3 attempt 1 failed ({exc!r}); retrying")
                time.sleep(15)
            else:
                report.append(f"! rs3 automation failed: {exc!r}")
    return report


def _connect_when_ready(cfg: Config, deadline_s: float = 180.0):
    """Connect to the RS3 window, polling short connects until it exists or the
    deadline passes; returns the Application or None.

    A single connect(timeout=N) that expires raises TimeoutError and kills the
    whole attempt - the original failure mode. Polling makes the launch path
    robust to an unpredictable cold-start time.
    """
    from pywinauto import Application
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto.timings import TimeoutError as UIATimeoutError

    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            return Application(backend="uia").connect(
                title_re=cfg.rs3.window_title_re, timeout=3
            )
        except (ElementNotFoundError, UIATimeoutError):
            time.sleep(3)
    return None


def _attempt_download(cfg: Config, report: list[str], ts: "_Troubleshoot | None" = None) -> None:
    try:
        from pywinauto import Application, Desktop
        from pywinauto.findwindows import ElementNotFoundError
        from pywinauto.timings import TimeoutError as UIATimeoutError
    except ImportError:
        report.append("! pywinauto not installed; run: uv sync")
        return

    if True:  # keep the original indentation/structure below
        try:
            app = Application(backend="uia").connect(
                title_re=cfg.rs3.window_title_re, timeout=5
            )
        # connect raises TimeoutError (not ElementNotFoundError) when no
        # window exists - both must route to the launch branch, otherwise a
        # crashed RS3 is never restarted.
        except (ElementNotFoundError, UIATimeoutError):
            if _rs3_process_running(cfg):
                # Process exists but the window didn't match: never launch a
                # duplicate instance; surface the mismatch instead.
                report.append(
                    "! RS3 process is running but no window matches "
                    f"rs3.window_title_re={cfg.rs3.window_title_re!r} - fix the pattern"
                )
                return
            if not cfg.rs3.exe_path or not cfg.rs3.exe_path.is_file():
                report.append(
                    "! Race Studio 3 is not running and rs3.exe_path is not set/found; "
                    "cannot trigger download"
                )
                return
            subprocess.Popen([str(cfg.rs3.exe_path)])
            report.append(f"launched {cfg.rs3.exe_path}; waiting for its window")
            # Poll instead of a fixed sleep+connect: RS3 cold start varies from
            # ~15s to well over a minute under load, so `sleep(30);
            # connect(timeout=60)` is exactly what produced the intermittent
            # TimeoutError twice. Keep trying short connects until the window
            # really exists or a generous deadline passes.
            app = _connect_when_ready(cfg, deadline_s=180.0)
            if app is None:
                report.append(
                    "! RS3 launched but no matching window appeared within 180s "
                    f"(rs3.window_title_re={cfg.rs3.window_title_re!r})"
                )
                return
            report.append("RS3 window is up")

        window = app.top_window()
        try:
            if window.is_minimized():
                window.restore()
            # Maximize for a deterministic layout on ANY screen resolution:
            # controls are found by NAME (not pixel positions), but a small
            # window can collapse toolbars and hide the buttons entirely.
            window.maximize()
        except Exception:  # noqa: BLE001 - restore/maximize are best-effort
            pass
        window.set_focus()
        if ts:
            ts.snap(window, "window")

        # RS3 pops unsolicited dialogs (e.g. "Upload to AiM?" asking to share
        # data with AiM's servers) that block every other control - clear
        # them first. Share/upload prompts are always declined.
        _dismiss_blocking_dialogs(window, report)

        # A connected device shows up as a TabItem like "MyChron6 Brim (USB)".
        device = _connected_device_name(window)
        if not device:
            if ts:
                ts.snap(window, "no_device")
                ts.dump(window, "no_device")
            report.append(
                "? no AiM device connected (USB or WiFi) - cannot download"
            )
            return

        # Open the DEVICE's Data Download sub-tab (the global "Data Download"
        # button is a different, mostly-disabled nav control).
        _open_device_download_view(window, device)
        _dismiss_blocking_dialogs(window, report)
        if ts:
            ts.snap(window, "download_view")
            ts.dump(window, "download_view")
            # Troubleshooting only: reveal already-downloaded sessions so the
            # list is populated even when there is nothing new, then scroll it
            # end-to-end capturing each page - that is how we see the whole
            # picture and learn the real control names/layout.
            _unhide_downloaded(window, report)
            ts.snap(window, "after_unhide")
            ts.dump(window, "after_unhide")
            _scroll_capture(window, ts)

        _click_named_button(window, "refresh list")
        time.sleep(4)
        _dismiss_blocking_dialogs(window, report)
        if ts:
            ts.snap(window, "after_refresh")

        # The session list is custom-drawn: nameless ListItem rows (group
        # headers + sessions) with a checkbox at each row's left edge and a
        # select-all header bar above. Select everything, then Data Download.
        if not _download_button_enabled(window):
            _select_all_sessions(window)
            time.sleep(2)
        if ts:
            ts.snap(window, "after_select_all")
            ts.dump(window, "after_select_all")
        if not _download_button_enabled(window):
            report.append(
                f"{device} connected: no new sessions to download "
                "(everything already downloaded)"
            )
            return

        if not _click_named_button(window, "data download"):
            report.append("? Data Download enabled but could not be clicked")
            return
        report.append(f"downloading sessions from {device}...")
        if ts:
            ts.snap(window, "download_clicked")
        # Short 30s waits while troubleshooting; the full 10-min wait in
        # production.
        confirm_s = 30.0
        finish_s = 30.0 if ts else 600.0
        _confirm_dialogs(app, window, report, duration_s=confirm_s)
        if _wait_download_finished(window, timeout_s=finish_s):
            report.append("MyChron download complete")
            if ts:
                ts.snap(window, "download_done")
        else:
            if ts:
                ts.snap(window, "download_timeout")
                report.append("? download not finished within 30s (troubleshoot cap)")
            else:
                report.append("? download still running after 10 min - leaving it to finish")


# Never click anything whose name suggests data removal - RS3 places
# "Delete" right next to "Data Download" - nor display toggles ("hide"
# covers both states of the Hide/Unhide Downloaded button, whose label
# contains "download" and would otherwise match the generic fallback).
_FORBIDDEN_WORDS = ("delete", "erase", "remove", "clear", "format", "hide")

# Dialogs offering to send data off the machine are always declined too
# (e.g. RS3's "Upload to AiM?" share-tracks nag).
_DECLINE_TOPICS = _FORBIDDEN_WORDS + ("upload", "share", "send to aim")

# Buttons that safely acknowledge/advance a dialog.
_CONFIRM_NAMES = ("ok", "yes", "confirm", "start", "start download", "continue", "proceed")
_DECLINE_NAMES = ("no", "cancel", "skip")


def choose_dialog_answer(dialog_texts: list[str]) -> str:
    """'confirm' or 'decline' for a dialog, based on what it says.

    Declined: anything suggesting data removal (RS3 offers 'erase memory
    after download' - the pipeline never deletes data) and anything offering
    to upload/share data with AiM's servers.
    """
    blob = " ".join(dialog_texts).lower()
    if any(w in blob for w in _DECLINE_TOPICS):
        return "decline"
    return "confirm"


def _dismiss_blocking_dialogs(window, report: list[str], rounds: int = 3) -> None:
    """Clear unsolicited modal dialogs (share/upload nags) blocking the UI.

    Only DECLINE-classified dialogs are dismissed here; anything else is left
    alone (we are not mid-download, so there is nothing to confirm).
    """
    for _ in range(rounds):
        try:
            dialogs = window.descendants(control_type="Window")
        except Exception:  # noqa: BLE001
            return
        acted = False
        for dlg in dialogs:
            try:
                texts = [
                    (t.window_text() or "").strip()
                    for t in dlg.descendants(control_type="Text")
                ]
                checkboxes = [
                    (c.window_text() or "").strip()
                    for c in dlg.descendants(control_type="CheckBox")
                ]
                if choose_dialog_answer(texts + checkboxes + [dlg.window_text() or ""]) != "decline":
                    continue
                names = {
                    (b.window_text() or "").strip().lower(): b
                    for b in dlg.descendants(control_type="Button")
                }
                for name in _DECLINE_NAMES:
                    b = names.get(name)
                    if b is not None and b.is_enabled():
                        b.click_input()
                        report.append("declined RS3 dialog (share/upload prompt)")
                        acted = True
                        break
            except Exception:  # noqa: BLE001 - a vanished dialog is fine
                continue
        if not acted:
            return
        time.sleep(2)


def _open_device_download_view(window, device: str) -> None:
    """Click the device tab, then its 'Data Download' sub-tab."""
    try:
        for tab in window.descendants(control_type="TabItem"):
            if (tab.window_text() or "").strip() == device:
                tab.click_input()
                time.sleep(2)
                break
        for tab in window.descendants(control_type="TabItem"):
            if (tab.window_text() or "").strip().lower() == "data download":
                tab.click_input()
                time.sleep(3)
                return
    except Exception:  # noqa: BLE001 - view may already be open
        pass


def _download_button_enabled(window) -> bool:
    try:
        for b in window.descendants(control_type="Button"):
            if (b.window_text() or "").strip().lower() == "data download":
                return b.is_enabled()
    except Exception:  # noqa: BLE001
        pass
    return False


def _session_list(window):
    """The custom-drawn session list: the List control with the most
    (nameless) ListItem rows. Returns (list_control, rows)."""
    best, best_rows = None, []
    try:
        for li in window.descendants(control_type="List"):
            rows = li.descendants(control_type="ListItem")
            if len(rows) > len(best_rows):
                best, best_rows = li, rows
    except Exception:  # noqa: BLE001
        pass
    return best, best_rows


def _select_all_sessions(window) -> None:
    """Select every downloadable session.

    The list is custom-drawn (nameless rows, checkbox at each row's left
    edge) with a SELECT-ALL checkbox in the header bar just above it.
    Clicking that header checkbox is the safe path (row-by-row clicking
    would toggle already-selected rows off and double-toggle group
    children). The Data Download button's enabled state is the only
    reliable signal that a selection exists - check it between attempts.
    """
    from pywinauto import mouse

    big, rows = _session_list(window)
    if big is None:
        return
    big_rect = big.rectangle()

    header = None
    try:
        for li in window.descendants(control_type="List"):
            r = li.rectangle()
            if (
                li is not big
                and abs(r.left - big_rect.left) < 40
                and 0 <= big_rect.top - r.bottom < 40
            ):
                header = li
                break
    except Exception:  # noqa: BLE001
        pass

    # Header select-all: toggle up to twice (first click may deselect when
    # everything was already selected).
    if header is not None:
        hr = header.rectangle()
        for _ in range(2):
            mouse.click(coords=(hr.left + 12, hr.top + hr.height() // 2))
            time.sleep(1.5)
            if _download_button_enabled(window):
                return

    # Fallback: single pass over group rows only (top-level 86 px rows),
    # ticking each group checkbox once selects all its sessions.
    _, rows = _session_list(window)
    for row in rows:
        try:
            r = row.rectangle()
            mouse.click(coords=(r.left + 12, r.top + r.height() // 2))
            time.sleep(0.3)
            if _download_button_enabled(window):
                return
        except Exception:  # noqa: BLE001 - row may have scrolled away
            continue


def _unhide_downloaded(window, report: list[str]) -> None:
    """Troubleshooting only: reveal already-downloaded sessions.

    RS3 hides sessions it has already downloaded behind a Hide/Unhide toggle,
    so the download list can look empty even when the device is full. The
    production flow must never touch it (the 'hide' guard in _FORBIDDEN_WORDS);
    while troubleshooting we want the full list visible. Click toward the SHOWN
    state only, never toward hiding.
    """
    try:
        controls = window.descendants()
    except Exception:  # noqa: BLE001
        return
    for c in controls:
        try:
            text = (c.window_text() or "").strip()
            low = text.lower()
            if "download" not in low:
                continue
            ctype = c.element_info.control_type
            # A button/menu item literally offering to reveal them.
            if "unhide" in low or ("show" in low and "hide" not in low):
                if c.is_enabled():
                    c.click_input()
                    report.append(f"clicked '{text}' (reveal downloaded)")
                    time.sleep(1.5)
                    return
            # A 'Hide downloaded' checkbox that is ticked -> untick to show.
            if ctype == "CheckBox" and "hide" in low:
                try:
                    if c.get_toggle_state() == 1:
                        c.click_input()
                        report.append(f"unchecked '{text}' (reveal downloaded)")
                        time.sleep(1.5)
                        return
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001 - a vanished control is fine
            continue
    report.append("~ no unhide/show-downloaded control found (see control dump)")


def _scroll_capture(window, ts: "_Troubleshoot") -> None:
    """Scroll the session list top-to-bottom, snapping each page, so the whole
    download view is captured even when it overflows one screen."""
    from pywinauto import mouse

    big, _ = _session_list(window)
    if big is None:
        ts.snap(window, "list_not_found")
        return
    try:
        r = big.rectangle()
        cx = (r.left + r.right) // 2
        cy = (r.top + r.bottom) // 2
    except Exception:  # noqa: BLE001
        return
    # Wheel back to the top first (positive = up).
    for _ in range(12):
        try:
            mouse.scroll(coords=(cx, cy), wheel_dist=3)
        except Exception:  # noqa: BLE001
            break
    time.sleep(0.5)
    ts.snap(window, "list_top")
    # Page down through the list, one screen-ful at a time.
    for i in range(1, 7):
        for _ in range(3):
            try:
                mouse.scroll(coords=(cx, cy), wheel_dist=-3)
            except Exception:  # noqa: BLE001
                break
        time.sleep(0.4)
        ts.snap(window, f"list_page_{i}")


def _wait_download_finished(window, timeout_s: float = 600.0) -> bool:
    """During a download the toolbar shows 'Cancel'; it reverts to
    'Data Download' when the transfer completes."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            names = {
                (b.window_text() or "").strip().lower()
                for b in window.descendants(control_type="Button")
            }
        except Exception:  # noqa: BLE001 - transient UIA hiccup mid-transfer
            continue
        if "cancel" not in names and "data download" in names:
            time.sleep(3)  # let RS3 finish writing files
            return True
    return False


def _connected_device_name(window) -> str | None:
    """Name of a connected AiM device tab, e.g. 'MyChron6 Brim (USB)'."""
    try:
        for tab in window.descendants(control_type="TabItem"):
            text = (tab.window_text() or "").strip()
            if "mychron" in text.lower() or "(usb)" in text.lower():
                return text
    except Exception:  # noqa: BLE001
        pass
    return None


def _click_named_button(window, name: str) -> bool:
    try:
        for b in window.descendants(control_type="Button"):
            if (b.window_text() or "").strip().lower() == name and b.is_enabled():
                b.click_input()
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _download_button_disabled(window, cfg: Config) -> bool:
    """True when a configured download button exists but is disabled."""
    try:
        wanted = {n.lower() for n in cfg.rs3.download_button_names}
        for b in window.descendants(control_type="Button"):
            if (b.window_text() or "").strip().lower() in wanted:
                return not b.is_enabled()
    except Exception:  # noqa: BLE001
        pass
    return False


def _confirm_dialogs(app, window, report: list[str], duration_s: float = 90.0) -> None:
    """While the download runs, acknowledge dialogs RS3 pops up.

    Scans the main window and any extra app windows; clicks OK/Yes/etc.
    unless the dialog text mentions data removal, in which case No/Cancel.
    Stops after a few quiet scans or duration_s.
    """
    deadline = time.monotonic() + duration_s
    quiet_scans = 0
    while time.monotonic() < deadline and quiet_scans < 3:
        time.sleep(3)
        acted = False
        try:
            surfaces = [window] + [w for w in app.windows() if w.handle != window.handle]
        except Exception:  # noqa: BLE001
            surfaces = [window]
        for surface in surfaces:
            try:
                buttons = surface.descendants(control_type="Button")
                texts = [
                    (t.window_text() or "").strip()
                    for t in surface.descendants(control_type="Text")
                ]
                names = {(b.window_text() or "").strip().lower(): b for b in buttons}
                answer = choose_dialog_answer(texts)
                wanted = _CONFIRM_NAMES if answer == "confirm" else _DECLINE_NAMES
                for name in wanted:
                    b = names.get(name)
                    if b is not None and b.is_enabled():
                        b.click_input()
                        report.append(
                            f"answered dialog with '{b.window_text().strip()}'"
                            + (" (declined: mentions data removal)" if answer == "decline" else "")
                        )
                        acted = True
                        break
            except Exception:  # noqa: BLE001 - a vanished dialog is fine
                continue
        quiet_scans = 0 if acted else quiet_scans + 1


def _click_download_button(window, cfg: Config, report: list[str], exclude=frozenset()):
    """Click the best matching enabled download button. Returns its (lowercased)
    name, or None if nothing safe matched."""
    buttons = window.descendants(control_type="Button")

    def name_of(b) -> str:
        return (b.window_text() or "").strip().lower()

    def safe(b) -> bool:
        t = name_of(b)
        return (
            bool(t)
            and t not in exclude
            and not any(w in t for w in _FORBIDDEN_WORDS)
            and b.is_enabled()
        )

    # Exact configured names first, then any remaining button mentioning
    # "download".
    for candidate in cfg.rs3.download_button_names:
        for b in buttons:
            if name_of(b) == candidate.lower() and safe(b):
                b.click_input()
                report.append(f"clicked '{b.window_text().strip()}' in Race Studio 3")
                return name_of(b)
    for b in buttons:
        if "download" in name_of(b) and safe(b):
            b.click_input()
            report.append(f"clicked '{b.window_text().strip()}' in Race Studio 3")
            return name_of(b)
    return None


def _rs3_process_running(cfg: Config) -> bool:
    import psutil

    exe_name = cfg.rs3.exe_path.name.lower() if cfg.rs3.exe_path else "aimrs3"
    for proc in psutil.process_iter(["name"]):
        name = (proc.info["name"] or "").lower()
        if exe_name in name or name.startswith("aimrs3"):
            return True
    return False
