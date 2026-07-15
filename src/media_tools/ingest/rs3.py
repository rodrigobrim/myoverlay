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

from ..config import Config


def trigger_rs3_download(cfg: Config) -> list[str]:
    if not cfg.rs3.enabled:
        return ["rs3 automation disabled (set [rs3] enabled = true)"]

    report: list[str] = []
    try:
        from pywinauto import Application, Desktop
        from pywinauto.findwindows import ElementNotFoundError
    except ImportError:
        return ["! pywinauto not installed; run: uv sync"]

    try:
        try:
            app = Application(backend="uia").connect(
                title_re=cfg.rs3.window_title_re, timeout=5
            )
        except ElementNotFoundError:
            if _rs3_process_running(cfg):
                # Process exists but the window didn't match: never launch a
                # duplicate instance; surface the mismatch instead.
                return [
                    "! RS3 process is running but no window matches "
                    f"rs3.window_title_re={cfg.rs3.window_title_re!r} - fix the pattern"
                ]
            if not cfg.rs3.exe_path or not cfg.rs3.exe_path.is_file():
                return [
                    "! Race Studio 3 is not running and rs3.exe_path is not set/found; "
                    "cannot trigger download"
                ]
            subprocess.Popen([str(cfg.rs3.exe_path)])
            report.append(f"launched {cfg.rs3.exe_path}")
            time.sleep(20)  # RS3 startup is slow
            app = Application(backend="uia").connect(
                title_re=cfg.rs3.window_title_re, timeout=30
            )

        window = app.top_window()
        try:
            if window.is_minimized():
                window.restore()
        except Exception:  # noqa: BLE001 - restore is best-effort
            pass
        window.set_focus()

        # Level 1: open RS3's download view (the "Data Download" nav button).
        clicked = _click_download_button(window, cfg, report)
        if clicked is None:
            report.append(
                "? no download button found in RS3 - the UI may have changed; "
                "adjust rs3.download_button_names"
            )
            return report

        # Level 2: with a device in WiFi range, the download view shows
        # per-device download controls; give it a moment and click one.
        time.sleep(3)
        second = _click_download_button(window, cfg, report, exclude={clicked})
        if second:
            report.append("MyChron download triggered")
            _confirm_dialogs(app, window, report)
        else:
            report.append(
                "? download view open, no device-level download available "
                "(MyChron not in WiFi range?)"
            )
    except Exception as exc:  # noqa: BLE001 - automation must never kill the pipeline
        report.append(f"! rs3 automation failed: {exc!r}")
    return report


# Never click anything whose name suggests data removal, whatever the config
# says - RS3 places "Delete" right next to "Data Download".
_FORBIDDEN_WORDS = ("delete", "erase", "remove", "clear", "format", "unhide")

# Buttons that safely acknowledge/advance a dialog.
_CONFIRM_NAMES = ("ok", "yes", "confirm", "start", "start download", "continue", "proceed")
_DECLINE_NAMES = ("no", "cancel", "skip")


def choose_dialog_answer(dialog_texts: list[str]) -> str:
    """'confirm' or 'decline' for a dialog, based on what it says.

    Any dialog whose text suggests removing data from the device (RS3 offers
    'erase memory after download') must be DECLINED - the pipeline never
    deletes data, on disk or on the MyChron.
    """
    blob = " ".join(dialog_texts).lower()
    if any(w in blob for w in _FORBIDDEN_WORDS):
        return "decline"
    return "confirm"


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
