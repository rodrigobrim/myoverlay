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

import re
import subprocess
import time
from pathlib import Path

from ..config import Config

# Known RS3 install locations, tried when rs3.exe_path is not set. The real
# 3.83 binary is 64/AiMRS3-64-ReleaseU.exe - NOT the RaceStudio3.exe the old
# example config suggested.
_RS3_EXE_CANDIDATES = (
    Path("C:/AIM_SPORT/RaceStudio3/64/AiMRS3-64-ReleaseU.exe"),
    Path("C:/AIM_SPORT/RaceStudio3/RaceStudio3.exe"),
)


def find_rs3_exe(cfg: Config) -> Path | None:
    """The RS3 executable: configured path first, then known install spots."""
    if cfg.rs3.exe_path and cfg.rs3.exe_path.is_file():
        return cfg.rs3.exe_path
    for candidate in _RS3_EXE_CANDIDATES:
        if candidate.is_file():
            return candidate
    root = Path("C:/AIM_SPORT/RaceStudio3")
    if root.is_dir():
        for hit in sorted(root.glob("**/AiMRS3*.exe")):
            if hit.is_file():
                return hit
    return None


def version_from_title(title: str) -> str | None:
    """'RaceStudio3 (64 bit) 3.83.39' -> '3.83.39'."""
    m = re.search(r"(\d+\.\d+\.\d+)", title or "")
    return m.group(1) if m else None


def rs3_installed_version(cfg: Config) -> str | None:
    """Version of the installed RS3 exe (file metadata), or None."""
    exe = find_rs3_exe(cfg)
    if exe is None:
        return None
    try:
        import win32api

        info = win32api.GetFileVersionInfo(str(exe), "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
    except Exception:  # noqa: BLE001 - metadata may be absent
        return None


def rs3_version_supported(version: str | None, cfg: Config) -> bool:
    """True when this RS3 version was validated against real UI snapshots.

    An unknown (None) version is treated as UNSUPPORTED: the automation
    clicks by control name in a UI AiM redesigns at will, and a mis-click
    mid-transfer can wedge the MyChron - never drive a layout we have not
    seen. An empty validated_versions list disables the check.
    """
    if not cfg.rs3.validated_versions:
        return True
    return version in cfg.rs3.validated_versions


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


class _Report(list):
    """Report lines, optionally echoed the moment they are appended.

    The RS3 flow can legitimately run for many minutes (cold app start, a
    long device transfer). Buffering everything and printing at the end
    reads as a silent hang from the console - the echo callback lets the
    CLI stream each line live while the returned list stays intact for
    callers that want the full report afterwards.
    """

    def __init__(self, echo=None) -> None:
        super().__init__()
        self._echo = echo

    def append(self, line: str) -> None:
        super().append(line)
        if self._echo is not None:
            try:
                self._echo(line)
            except Exception:  # noqa: BLE001 - display must never break the run
                pass


def trigger_rs3_download(
    cfg: Config, troubleshoot: bool = False, echo=None, ask=None
) -> list[str]:
    """Drive RS3 to download from the MyChron. Retries once: RS3 crashes and
    UIA/COM hiccups (busy repaint, app restart) are transient in practice.

    troubleshoot=True runs a single, heavily-instrumented pass (snapshots +
    control dumps at every step, unhides already-downloaded sessions, scrolls
    the list) with short 30s waits, to understand/refine the procedure.

    echo, when given, receives each report line as soon as it is produced.

    ask, when given, is called as ask(question, options) -> index or None, and
    is how the WiFi flow asks which MyChron to use. Without it the run is
    non-interactive (a scheduled task has no console): an ambiguous or absent
    device is reported and the run stops rather than guessing.
    """
    if not cfg.rs3.enabled:
        return ["rs3 automation disabled (set [rs3] enabled = true)"]

    report: list[str] = _Report(echo)
    ts = _Troubleshoot(cfg) if troubleshoot else None
    # Set by the WiFi flow so the link is always dropped again, whichever way
    # the run ends - while RS3 holds the MyChron's WiFi the PC has no
    # internet, so leaving it connected breaks everything downstream.
    link = {"connected": False, "window": None}
    # Troubleshooting is a single deliberate pass; don't blur it with a retry.
    attempts = (1,) if ts else (1, 2)
    try:
        for attempt in attempts:
            try:
                _attempt_download(cfg, report, ts, ask, link)
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
    finally:
        if link["connected"] and link["window"] is not None:
            try:
                disconnect_wifi_device(link["window"], report)
            except Exception:  # noqa: BLE001 - cleanup must never raise
                report.append(
                    "~ could not disconnect the MyChron - disconnect it in RS3 "
                    "by hand to get the WiFi back"
                )


def _connect_when_ready(cfg: Config, deadline_s: float = 180.0, report: list[str] | None = None):
    """Connect to the RS3 window, polling short connects until it exists or the
    deadline passes; returns the Application or None.

    A single connect(timeout=N) that expires raises TimeoutError and kills the
    whole attempt - the original failure mode. Polling makes the launch path
    robust to an unpredictable cold-start time.
    """
    from pywinauto import Application
    from pywinauto.findwindows import ElementNotFoundError
    from pywinauto.timings import TimeoutError as UIATimeoutError

    start = time.monotonic()
    deadline = start + deadline_s
    last_beat = start
    while time.monotonic() < deadline:
        try:
            return Application(backend="uia").connect(
                title_re=cfg.rs3.window_title_re, timeout=3
            )
        except (ElementNotFoundError, UIATimeoutError):
            now = time.monotonic()
            if report is not None and now - last_beat >= 30:
                report.append(f"still waiting for the RS3 window ({now - start:.0f}s)...")
                last_beat = now
            time.sleep(3)
    return None


def _attempt_download(
    cfg: Config,
    report: list[str],
    ts: "_Troubleshoot | None" = None,
    ask=None,
    link: dict | None = None,
) -> None:
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
            exe = find_rs3_exe(cfg)
            if exe is None:
                report.append(
                    "! Race Studio 3 is not running and no RS3 executable was "
                    "found (set rs3.exe_path); cannot trigger download"
                )
                return
            installed = rs3_installed_version(cfg)
            if not rs3_version_supported(installed, cfg):
                report.append(
                    f"! installed RS3 version {installed or 'unknown'} is not validated "
                    f"for automation (validated: {', '.join(cfg.rs3.validated_versions)}); "
                    "refusing to drive an unvalidated UI - update RS3 or set "
                    "[rs3] validated_versions"
                )
                return
            subprocess.Popen([str(exe)])
            report.append(f"launched {exe}; waiting for its window")
            # Poll instead of a fixed sleep+connect: RS3 cold start varies from
            # ~15s to well over a minute under load, so `sleep(30);
            # connect(timeout=60)` is exactly what produced the intermittent
            # TimeoutError twice. Keep trying short connects until the window
            # really exists or a generous deadline passes.
            app = _connect_when_ready(cfg, deadline_s=180.0, report=report)
            if app is None:
                report.append(
                    "! RS3 launched but no matching window appeared within 180s "
                    f"(rs3.window_title_re={cfg.rs3.window_title_re!r})"
                )
                return
            report.append("RS3 window is up")

        window = app.top_window()
        # Gate on the RUNNING instance's version (title carries it): only
        # layouts validated against real UI snapshots are ever driven.
        running = version_from_title(window.window_text() or "")
        if not rs3_version_supported(running, cfg):
            report.append(
                f"! running RS3 version {running or 'unknown'} is not validated for "
                f"automation (validated: {', '.join(cfg.rs3.validated_versions)}); "
                "refusing to drive an unvalidated UI - update RS3 or set "
                "[rs3] validated_versions"
            )
            return
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
            # Nothing on USB: the MyChron may still be reachable over its own
            # WiFi. Offer/perform the connect before giving up.
            if cfg.rs3.wifi_connect:
                device = _connect_over_wifi(cfg, window, report, ts, ask)
                if device and link is not None:
                    # We took the WiFi; the caller drops it again whatever
                    # happens from here on.
                    link["connected"] = True
                    link["window"] = window
            if not device:
                if cfg.rs3.wifi_connect:
                    # _connect_over_wifi already said why.
                    return
                report.append(
                    "? no AiM device connected (USB or WiFi) - cannot download"
                )
                return

        # Open the DEVICE's Data Download sub-tab (the global "Data Download"
        # button is a different, mostly-disabled nav control).
        _open_device_download_view(window, device)
        _dismiss_blocking_dialogs(window, report)

        # A transfer may already be running (a retry after a UIA hiccup, a
        # human click, a previous run's transfer still going). Driving the
        # UI on top of an active device transfer is exactly the kind of
        # interference that wedges the MyChron - wait it out instead.
        if _transfer_in_progress(window):
            report.append(
                f"{device}: a transfer is already in progress - waiting for it "
                "instead of starting a new one"
            )
            if _wait_download_finished(window, timeout_s=600.0, report=report):
                report.append("in-progress transfer finished")
            else:
                report.append("? transfer still running after 10 min - leaving it to finish")
            return
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
        if _wait_download_finished(window, timeout_s=finish_s, report=report):
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
# contains "download" and would otherwise match the generic fallback) -
# nor anything touching device firmware or configuration.
_FORBIDDEN_WORDS = (
    "delete", "erase", "remove", "clear", "format", "hide",
    "firmware", "firmup",
)

# Dialogs offering to send data off the machine are always declined too
# (e.g. RS3's "Upload to AiM?" share-tracks nag), as is anything about
# device configuration (firmware updates, WiFi settings): a download run
# must never reconfigure the MyChron.
_DECLINE_TOPICS = _FORBIDDEN_WORDS + (
    "upload", "share", "send to aim", "wifi", "wi-fi",
)

# Buttons that safely acknowledge/advance a dialog.
_CONFIRM_NAMES = ("ok", "yes", "confirm", "start", "start download", "continue", "proceed")
_DECLINE_NAMES = ("no", "cancel", "skip")


def choose_dialog_answer(dialog_texts: list[str]) -> str:
    """'confirm' or 'decline' for a dialog, based on what it says.

    Declined: anything suggesting data removal (RS3 offers 'erase memory
    after download' - the pipeline never deletes data), anything offering
    to upload/share data with AiM's servers, and anything touching device
    configuration (firmware, WiFi) - a download run must never reconfigure
    the MyChron.
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


def _transfer_in_progress(window) -> bool:
    """True while a device transfer is running: the toolbar's 'Data Download'
    button is replaced by 'Cancel' for the duration (see
    _wait_download_finished, which watches the reverse transition)."""
    try:
        names = {
            (b.window_text() or "").strip().lower()
            for b in window.descendants(control_type="Button")
        }
        return "cancel" in names and "data download" not in names
    except Exception:  # noqa: BLE001
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


def _wait_download_finished(
    window, timeout_s: float = 600.0, report: list[str] | None = None
) -> bool:
    """During a download the toolbar shows 'Cancel'; it reverts to
    'Data Download' when the transfer completes.

    Poll interval is deliberately coarse: each check walks RS3's whole UIA
    control tree via a synchronous COM call into its UI thread. Hammering
    that every few seconds for the full multi-minute transfer contends with
    the same thread that services the USB link to the MyChron, which has
    reproduced the device going unresponsive / corrupting sessions mid
    download - the same symptom as VirtualBox's USB layer starving it, just
    from RS3's own side this time.
    """
    start = time.monotonic()
    deadline = start + timeout_s
    last_beat = start
    while time.monotonic() < deadline:
        time.sleep(20)
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
        now = time.monotonic()
        if report is not None and now - last_beat >= 60:
            report.append(f"transfer in progress ({now - start:.0f}s)...")
            last_beat = now
    return False


def _connected_device_name(window) -> str | None:
    """Name of the connected AiM device, e.g. 'MyChron6 Brim (USB)'.

    RS3 3.83.39 exposes the "Connected Devices" pane as rows whose UIA name is
    whitespace - the label is drawn, not published - so a name found there is
    used when present and the pane is otherwise read by OCR. Missing this is
    what made a successful WiFi connect look like a failure.
    """
    try:
        for tab in window.descendants(control_type="TabItem"):
            text = (tab.window_text() or "").strip()
            low = text.lower()
            if "mychron" in low or "(usb)" in low or "(wifi)" in low:
                return text
    except Exception:  # noqa: BLE001
        pass
    return _connected_device_by_ocr(window)


# The device pane sits in the top-left of the main window; the device page
# (and its error banner) fills the area to the right of the splitter.
def _connected_device_by_ocr(window) -> str | None:
    """Read the Connected Devices pane with OCR; None when it says nothing."""
    text = _ocr_region(_device_pane_rect(window))
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        low = line.lower()
        if not line or "connected devices" in low:
            continue
        if "no device" in low:
            return None
        # The pane lists the device by name once it is actually linked.
        if "mychron" in low or "aim" in low:
            return line
    return None


def device_page_has_comms_error(text: str) -> bool:
    """True for RS3's red "Can't communicate..." banner on the device page.

    The device answers the WiFi scan and RS3 lists it as connected, but the
    data link never comes up - downloading in that state is what leaves the
    MyChron wedged, so the run must stop and disconnect instead.
    """
    low = (text or "").lower()
    return "can't communicate" in low or "cant communicate" in low


def _device_pane_rect(window):
    try:
        rect = window.rectangle()
    except Exception:  # noqa: BLE001
        return None
    # Left-hand device pane, below the toolbar.
    return (rect.left + 3, rect.top + 79, rect.left + 690, rect.top + 400)


def _device_page_rect(window):
    try:
        rect = window.rectangle()
    except Exception:  # noqa: BLE001
        return None
    return (rect.left + 690, rect.top + 79, rect.right - 3, rect.top + 400)


def _ocr_region(bbox) -> str:
    """OCR a screen region; '' when anything at all goes wrong."""
    if bbox is None:
        return ""
    try:
        import tempfile

        from PIL import ImageGrab

        shot = ImageGrab.grab(bbox=bbox, all_screens=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "region.png"
            shot.save(path)
            return _windows_ocr(path)
    except Exception:  # noqa: BLE001
        return ""


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

    ONLY true dialog windows are scanned: child Window controls of the main
    window, plus extra top-level app windows. The main window itself is
    NEVER a surface - its device tabs (WiFi and Properties, Settings,
    Firmware) are full of buttons that send commands to the MyChron, and an
    earlier version that scanned it clicked one mid-transfer ("answered
    dialog with ''"), which is the prime suspect for the device ending up
    wedged with corrupted WiFi config. Buttons whose current name is empty
    are never clicked, for the same reason: we cannot know what they do.

    Dialogs mentioning data removal or device configuration get No/Cancel;
    anything else gets OK/Yes. Stops after a few quiet scans or duration_s.

    Poll interval is deliberately coarse - see _wait_download_finished for
    why hammering RS3's UI thread mid-transfer is unsafe.
    """
    deadline = time.monotonic() + duration_s
    quiet_scans = 0
    while time.monotonic() < deadline and quiet_scans < 3:
        time.sleep(8)
        acted = False
        surfaces = []
        try:
            surfaces += window.descendants(control_type="Window")
        except Exception:  # noqa: BLE001
            pass
        try:
            surfaces += [w for w in app.windows() if w.handle != window.handle]
        except Exception:  # noqa: BLE001
            pass
        for dlg in surfaces:
            try:
                texts = [
                    (t.window_text() or "").strip()
                    for t in dlg.descendants(control_type="Text")
                ]
                names = {
                    (b.window_text() or "").strip().lower(): b
                    for b in dlg.descendants(control_type="Button")
                }
                answer = choose_dialog_answer(texts + [dlg.window_text() or ""])
                wanted = _CONFIRM_NAMES if answer == "confirm" else _DECLINE_NAMES
                for name in wanted:
                    b = names.get(name)
                    if b is None or not b.is_enabled():
                        continue
                    label = (b.window_text() or "").strip()
                    if not label:
                        continue
                    b.click_input()
                    blurb = " | ".join(t for t in texts if t)[:120]
                    report.append(
                        f"answered dialog [{blurb}] with '{label}'"
                        + (" (declined)" if answer == "decline" else "")
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


# ---------------------------------------------------------------------------
# WiFi connect ("Available devices" dialog)
#
# Learned by probing a live 3.83.39 install (see docs/rs3-automation.md):
#
#   * The dialog is opened by the WiFi icon in the main window's top-right
#     toolbar. Every toolbar icon is a nameless Button; the ONLY thing that
#     identifies it is its tooltip, so the tooltip is read and matched before
#     clicking. Its automation_id (2519 here) is a fallback hint, never the
#     sole criterion - ids shift between builds.
#   * The dialog is a child Window of the main window titled "Available
#     devices". It is NOT reliably reachable through Desktop().windows().
#   * Its rows are owner-drawn: no text via UIA, and the underlying ListBox
#     holds pointers rather than strings (LB_GETTEXT returns garbage), so row
#     labels can only be read by OCR of the row bitmap.
#   * Rows come in two heights: section headers ("AiM devices", "Other
#     networks") are shorter than device/network rows. That is the structural
#     way to tell a header from a selectable entry.
#   * Typing the AiM SSID prefix into the search box filters the list down to
#     AiM devices only - which is how the device rows are isolated without
#     reading a single label.
#   * "View:" is a CYCLING filter (all -> only mine -> ...), not a menu. It is
#     never clicked: cycling it hides the device and pops an info dialog.
#     "Settings..." is likewise never touched - it configures the device.
# ---------------------------------------------------------------------------

_WIFI_TOOLTIPS = ("wifi", "wi-fi")
_WIFI_TOOLBAR_AUTOMATION_ID = "2519"
_AVAILABLE_DEVICES_TITLE = "available devices"


def _connect_over_wifi(
    cfg: Config, window, report: list[str], ts: "_Troubleshoot | None" = None, ask=None
) -> str | None:
    """Connect to a MyChron over WiFi; returns the connected device name.

    One device -> connect it. Several -> ask which (never guess: connecting
    drives someone else's logger). None -> ask the user to make one available
    and retry once they confirm. Without an `ask` callback the run is
    non-interactive and anything ambiguous is reported instead of chosen.
    """
    dlg = _open_available_devices(window, report)
    if dlg is None:
        return None
    connect_clicked = False
    try:
        devices = _scan_for_aim_devices(cfg, dlg, report)

        # Nothing found: let the user power the MyChron up / wake its WiFi,
        # then rescan. This is the "ask for the user to connect it" case.
        if not devices and ask is not None:
            answer = ask(
                "No MyChron found over WiFi. Turn it on (and enable its WiFi), "
                "then choose:",
                ["Rescan now", "Give up"],
            )
            if answer == 0:
                devices = _scan_for_aim_devices(cfg, dlg, report, rescan=True)
        if not devices:
            report.append(
                "? no AiM device on USB and none broadcasting WiFi - turn the "
                "MyChron on (WiFi enabled) and run again"
            )
            return None

        if len(devices) == 1:
            chosen = devices[0]
        elif ask is None:
            names = ", ".join(d.label for d in devices)
            report.append(
                f"? {len(devices)} AiM devices are reachable over WiFi ({names}) "
                "- rerun interactively to choose one, or leave only one on"
            )
            return None
        else:
            idx = ask(
                "More than one MyChron is reachable over WiFi. Which one?",
                [d.label for d in devices],
            )
            if idx is None or not 0 <= idx < len(devices):
                report.append("? no device chosen - skipping the WiFi download")
                return None
            chosen = devices[idx]

        report.append(f"connecting to {chosen.label} over WiFi...")
        # Remember the network the PC is on NOW: connecting to the MyChron
        # replaces it with the logger's AP, and Windows does not always
        # reassociate by itself after the disconnect.
        global _wlan_before_connect
        _wlan_before_connect = _current_wlan_ssid()
        if ts:
            ts.snap(dlg, "wifi_before_connect")
        if not _click_row_and_connect(dlg, chosen, report):
            return None
        connect_clicked = True
    finally:
        # On abandon/failure, leave the dialog closed and its search filter
        # clean. But NEVER right after Connect: the handshake is running on
        # the same UI thread, and typing/clicking in the dialog at that moment
        # is what leaves the device in the "Can't communicate" state. RS3
        # keeps working fine with the dialog open; it is tidied up after the
        # link settles (or on disconnect).
        if not connect_clicked:
            _close_available_devices(dlg, report)

    device = _wait_for_connected_device(
        window, timeout_s=cfg.rs3.wifi_connect_timeout_s, report=report
    )
    # The handshake is over (either way) - now it is safe to tidy the dialog.
    _close_available_devices(dlg, report)
    if device is None:
        report.append(
            f"? {chosen.label} did not come up in RS3 within "
            f"{cfg.rs3.wifi_connect_timeout_s:.0f}s - it may be out of range or "
            "already talking to another PC"
        )
        # It may still have half-connected and taken the WiFi adapter with it.
        disconnect_wifi_device(window, report)
        return None

    # RS3 can list the device as connected while the data link is dead ("Can't
    # communicate, try reconnecting or restarting your device"). Downloading
    # then is what wedges the MyChron: stop, hand the WiFi back, say so.
    if device_page_has_comms_error(_ocr_region(_device_page_rect(window))):
        report.append(
            f"! {device} connected but RS3 reports 'Can't communicate' - not "
            "downloading in that state; restart the MyChron and try again"
        )
        disconnect_wifi_device(window, report)
        return None

    report.append(f"connected over WiFi: {device}")
    if ts:
        ts.snap(window, "wifi_connected")
    return device


class _DeviceRow:
    """A selectable AiM device row: where to click, and what to call it."""

    def __init__(self, index: int, rect, label: str) -> None:
        self.index = index
        self.rect = rect
        self.label = label


def _open_available_devices(window, report: list[str]):
    """Click the toolbar's WiFi icon and return the dialog, or None."""
    dlg = _find_available_devices(window)
    if dlg is not None:
        return dlg

    button = _wifi_toolbar_button(window)
    if button is None:
        report.append(
            "! could not find RS3's WiFi toolbar button (no icon reported a "
            "WiFi tooltip) - cannot open the device list"
        )
        return None
    try:
        button.click_input()
    except Exception as exc:  # noqa: BLE001
        report.append(f"! clicking RS3's WiFi button failed: {exc!r}")
        return None

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(1.5)
        dlg = _find_available_devices(window)
        if dlg is not None:
            return dlg
    report.append("! RS3's 'Available devices' dialog did not open")
    return None


def _find_available_devices(window):
    try:
        for child in window.descendants(control_type="Window"):
            if _AVAILABLE_DEVICES_TITLE in (child.window_text() or "").lower():
                return child
    except Exception:  # noqa: BLE001
        pass
    return None


def _wifi_toolbar_button(window):
    """The toolbar icon that opens the device list.

    Toolbar buttons carry no name, so each is hovered and its tooltip read -
    that is the only self-describing thing about them. The known automation
    id is tried first as a hint, but it is still confirmed by tooltip, so a
    renumbered build degrades to the (slower) scan instead of clicking a
    button whose purpose we do not know.
    """
    from pywinauto import mouse

    try:
        buttons = [
            b
            for b in window.descendants(control_type="Button")
            if (b.element_info.automation_id or "").isdigit()
            and b.rectangle().width() > 20
        ]
    except Exception:  # noqa: BLE001
        return None

    buttons.sort(
        key=lambda b: (b.element_info.automation_id or "") != _WIFI_TOOLBAR_AUTOMATION_ID
    )
    for button in buttons:
        try:
            rect = button.rectangle()
            mouse.move(coords=((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2))
            time.sleep(1.2)
            if any(t in _tooltip_text(window) for t in _WIFI_TOOLTIPS):
                return button
        except Exception:  # noqa: BLE001
            continue
    return None


def _tooltip_text(window) -> str:
    """Lowercased text of any tooltip currently showing."""
    parts: list[str] = []
    try:
        for tip in window.descendants(control_type="ToolTip"):
            parts.append((tip.window_text() or "").strip())
    except Exception:  # noqa: BLE001
        pass
    try:
        from pywinauto import Desktop

        for top in Desktop(backend="uia").windows():
            if top.element_info.control_type == "ToolTip":
                parts.append((top.window_text() or "").strip())
    except Exception:  # noqa: BLE001
        pass
    return " ".join(parts).lower()


def _scan_for_aim_devices(
    cfg: Config, dlg, report: list[str], rescan: bool = False
) -> list[_DeviceRow]:
    """AiM device rows currently listed, filtered away from house networks."""
    if rescan:
        if _click_named_button(dlg, "rescan"):
            report.append("rescanning for AiM devices...")
            time.sleep(20)
    _filter_search(dlg, cfg.rs3.wifi_device_prefix)
    time.sleep(2)

    rows = _visible_rows(dlg)
    if not rows:
        return []
    # Section headers are shorter than selectable entries; with the AiM filter
    # applied every remaining entry is an AiM device. When every row is the
    # same height there is nothing to tell a header from an entry, so nothing
    # is offered: connecting drives a real logger, and a wrong click there is
    # exactly what this module exists to avoid.
    heights = {r.height() for r in rows}
    if len(heights) < 2:
        return []
    tallest = max(heights)
    entries = [r for r in rows if r.height() >= tallest]
    return [
        _DeviceRow(i, rect, _row_label(dlg, rect, i))
        for i, rect in enumerate(entries)
    ]


def _visible_rows(dlg) -> list:
    rows = []
    try:
        for row in dlg.descendants(control_type="ListItem"):
            rect = row.rectangle()
            if rect.width() > 0 and rect.height() > 0:
                rows.append(rect)
    except Exception:  # noqa: BLE001
        return []
    return sorted(rows, key=lambda r: r.top)


def _search_box(dlg):
    try:
        edits = dlg.descendants(control_type="Edit")
        return edits[0] if edits else None
    except Exception:  # noqa: BLE001
        return None


def _filter_search(dlg, text: str) -> None:
    """Type `text` into the dialog's search box, replacing what is there."""
    edit = _search_box(dlg)
    if edit is None:
        return
    try:
        edit.click_input()
        time.sleep(0.4)
        edit.type_keys("^a{DELETE}")
        time.sleep(0.4)
        if text:
            edit.type_keys(text, with_spaces=True)
    except Exception:  # noqa: BLE001 - filtering is an optimisation, not a must
        pass


def _row_label(dlg, rect, index: int) -> str:
    """Human-readable name of a device row.

    The rows are owner-drawn, so the label exists only as pixels: OCR the row
    bitmap. Every part of this is best-effort - a device we cannot name is
    still perfectly connectable, it just gets a positional label.
    """
    fallback = f"AiM device #{index + 1}"
    try:
        import re as _re
        import tempfile

        from PIL import ImageGrab

        shot = ImageGrab.grab(
            bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "row.png"
            shot.save(path)
            text = _windows_ocr(path)
        if not text:
            return fallback
        # Keep the SSID-looking token; the row also carries "not mine" etc.
        for token in _re.findall(r"[A-Za-z0-9][\w.\- ]{4,}", text):
            token = token.strip()
            if token.lower().startswith("aim"):
                return token
        return text.strip().splitlines()[0][:60] or fallback
    except Exception:  # noqa: BLE001
        return fallback


# Windows' own OCR engine reads the owner-drawn rows. It is driven through
# PowerShell because the WinRT API has no dependency-free Python binding, and
# adding one for a label we can live without is not worth it.
_OCR_PS1 = r"""
param([string]$Path)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(15000) | Out-Null
    $task.Result
}
[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Media, ContentType = WindowsRuntime] | Out-Null
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { exit 1 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($line in $result.Lines) { $line.Text }
"""


def _windows_ocr(image_path: Path) -> str:
    """Text in an image per Windows' built-in OCR; '' when unavailable."""
    import tempfile

    try:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "ocr.ps1"
            script.write_text(_OCR_PS1, encoding="utf-8")
            proc = subprocess.run(
                [
                    "powershell", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-Path", str(image_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _click_row_and_connect(dlg, device: _DeviceRow, report: list[str]) -> bool:
    """Select the device row, then press Connect."""
    from pywinauto import mouse

    rect = device.rect
    try:
        # Click well inside the row's label area: the right-hand end carries
        # the "mine" toggle and the signal icon, which do other things.
        mouse.click(coords=(rect.left + 120, rect.top + rect.height() // 2))
        time.sleep(1.5)
    except Exception as exc:  # noqa: BLE001
        report.append(f"! could not select {device.label}: {exc!r}")
        return False

    connect = None
    try:
        for b in dlg.descendants(control_type="Button"):
            if (b.window_text() or "").strip().lower() == "connect":
                connect = b
                break
    except Exception:  # noqa: BLE001
        pass
    if connect is None:
        report.append("! no Connect button in the device list")
        return False
    if not connect.is_enabled():
        report.append(
            f"! Connect stayed disabled after selecting {device.label} - the row "
            "may have moved during the scan"
        )
        return False
    try:
        connect.click_input()
    except Exception as exc:  # noqa: BLE001
        report.append(f"! clicking Connect failed: {exc!r}")
        return False
    return True


def _close_available_devices(dlg, report: list[str]) -> None:
    """Clear the search filter and close the dialog. Never fails the run."""
    try:
        _filter_search(dlg, "")
        time.sleep(0.5)
        _click_named_button(dlg, "exit")
    except Exception:  # noqa: BLE001
        pass


# The house network the PC was on before the MyChron connect replaced it,
# so the disconnect can hand it back (see disconnect_wifi_device).
_wlan_before_connect: str | None = None


def _current_wlan_ssid() -> str | None:
    """SSID Windows' WLAN interface is associated to, or None.

    Only the `SSID :` line is matched (never `AP BSSID`); the label itself is
    not localised even though the rest of netsh's output is.
    """
    try:
        proc = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True, text=True, timeout=15,
        )
        for line in proc.stdout.splitlines():
            key, _, value = line.partition(":")
            if key.strip().upper() == "SSID" and value.strip():
                return value.strip()
    except Exception:  # noqa: BLE001
        pass
    return None


def _restore_wlan(report: list[str]) -> None:
    """Reassociate Windows to the pre-connect network if it did not itself."""
    wanted = _wlan_before_connect
    if not wanted:
        return
    try:
        if _current_wlan_ssid() == wanted:
            return
        subprocess.run(
            ["netsh", "wlan", "connect", f"name={wanted}"],
            capture_output=True, text=True, timeout=20,
        )
        time.sleep(6)
        if _current_wlan_ssid() == wanted:
            report.append(f"Windows WiFi back on '{wanted}'")
        else:
            report.append(
                f"~ could not rejoin WiFi network '{wanted}' - reconnect it "
                "by hand if the internet is down"
            )
    except Exception:  # noqa: BLE001 - restoring is best-effort cleanup
        pass


def disconnect_wifi_device(window, report: list[str]) -> bool:
    """Drop the WiFi link to the MyChron and hand the WiFi adapter back.

    While RS3 holds a WiFi device the PC's adapter is tied up with the
    logger's own network, so the machine has no internet - the rest of the
    pipeline (YouTube upload, updates) cannot run until this happens. Every
    path that connects must therefore also disconnect, success or not. After
    the RS3-side disconnect, Windows is pointed back at the network it was on
    before the connect (it does not always reassociate on its own).
    """
    dlg = _open_available_devices(window, report)
    if dlg is None:
        report.append(
            "! could not open the device list to disconnect - disconnect the "
            "MyChron in RS3 by hand to get the WiFi back"
        )
        return False
    try:
        # The connected row is the one whose action button reads Disconnect;
        # selecting it is what swaps Connect -> Disconnect.
        for attempt in range(2):
            if _click_named_button(dlg, "disconnect"):
                report.append("disconnected from the MyChron (WiFi released)")
                time.sleep(3)
                return True
            if attempt == 0:
                rows = _visible_rows(dlg)
                if not rows:
                    break
                heights = {r.height() for r in rows}
                if len(heights) < 2:
                    break
                tallest = max(heights)
                entry = next((r for r in rows if r.height() >= tallest), None)
                if entry is None:
                    break
                from pywinauto import mouse

                mouse.click(coords=(entry.left + 120, entry.top + entry.height() // 2))
                time.sleep(1.5)
        report.append(
            "~ no Disconnect control found - the MyChron may already be "
            "disconnected"
        )
        return False
    except Exception as exc:  # noqa: BLE001 - never fail the run over cleanup
        report.append(f"~ disconnect failed ({exc!r}); disconnect it by hand")
        return False
    finally:
        _close_available_devices(dlg, report)
        # Whatever happened above, the link may already be down with Windows
        # left associated to nothing - always offer the old network back.
        _restore_wlan(report)


# The WiFi handshake is the most fragile moment in the whole flow: RS3 is
# negotiating with the logger over its own access point on the same thread
# that serves its UI. Every UIA query is a synchronous COM call into that
# thread, and starving it there is what leaves the MyChron reporting "Can't
# communicate". So the wait is passive: a long settle with the UI untouched,
# then coarse polls - never the tight loop that would feel more responsive.
_WIFI_SETTLE_S = 25.0
_WIFI_POLL_S = 20.0


def _wait_for_connected_device(
    window, timeout_s: float = 120.0, report: list[str] | None = None
) -> str | None:
    """Wait for the device to appear in Connected Devices, touching nothing.

    See _WIFI_SETTLE_S: the link is left strictly alone while it comes up.
    """
    start = time.monotonic()
    deadline = start + timeout_s
    if report is not None:
        report.append("waiting for the WiFi link (leaving RS3 alone while it connects)...")
    time.sleep(min(_WIFI_SETTLE_S, timeout_s))

    last_beat = time.monotonic()
    while time.monotonic() < deadline:
        device = _connected_device_name(window)
        if device:
            return device
        now = time.monotonic()
        if report is not None and now - last_beat >= 60:
            report.append(f"still waiting for the WiFi link ({now - start:.0f}s)...")
            last_beat = now
        time.sleep(_WIFI_POLL_S)
    return None


def _rs3_process_running(cfg: Config) -> bool:
    import psutil

    exe_name = cfg.rs3.exe_path.name.lower() if cfg.rs3.exe_path else "aimrs3"
    for proc in psutil.process_iter(["name"]):
        name = (proc.info["name"] or "").lower()
        if exe_name in name or name.startswith("aimrs3"):
            return True
    return False
