"""The GUI's declared CLI coverage.

Every CLI command the GUI fronts is registered here, mapped to the GUI action
that invokes it. tests/test_gui_cli_parity.py diffs this registry (plus the
explicit exclusion lists it holds) against the full CLI command tree, so adding
a CLI command forces a conscious decision: expose it in the GUI and register it
here, or record it below with a reason.

Keep entries honest: register a command only when a real GUI action dispatches
it through mtclient.
"""

from __future__ import annotations

# Command path (as printed by docs_gen.iter_command_paths) -> the GUI action
# that invokes it.
GUI_COMMANDS: dict[str, str] = {
    "scan": "auto-run at startup; populates Gate 1 (app.ReviewApp._start)",
    "ingest": "Gate 1 'Confirm & Ingest' button (app.ReviewApp._ingest)",
    "plan": "auto-run after ingest; populates Gate 2 (app.ReviewApp._ingested)",
    "render": "Gate 2 'Confirm queue -> Render' button (app.ReviewApp._render)",
}

# Deliberately CLI-only: exposing these in the GUI makes no sense. Each needs
# a reason; the parity test rejects unexplained gaps.
CLI_ONLY: dict[str, str] = {
    "gui": "launches the GUI itself",
    "docs": "developer tooling (regenerates docs/CLI.md)",
    "watch": "background daemon; the GUI is the interactive alternative",
    "google-setup": "one-time machine setup, drives external browser windows",
    "google-auth": "one-time machine setup, drives external browser windows",
    "run": "headless full-chain shortcut; the GUI's gated flow replaces it",
}

# GUI backlog: should eventually be reachable from the GUI but is not yet.
# Moving an entry from here into GUI_COMMANDS is the TDD loop for GUI work.
PLANNED: dict[str, str] = {
    "video list remote": "pick videos to download from the card",
    "video list local": "browse videos already in the library",
    "video list all": "full card + library video overview",
    "video get": "selective video download (by file or day)",
    "telemetry list remote": "pick MyChron sessions to download",
    "telemetry list local": "pick already-downloaded MyChron sessions",
    "telemetry list all": "full device + disk session overview",
    "telemetry get": "telemetry-only download",
    "best-lap": "show best lap per session",
    "meta": "edit title/description/visibility of a published video",
    "correlate": "re-run session grouping for a day",
    "sync": "manual sync anchor entry when auto-sync fails",
    "join": "join camera-split segments",
    "slice": "cut highlight slices from a rendered video",
    "publish": "upload rendered videos to YouTube",
    "status": "pipeline state per track day",
}
