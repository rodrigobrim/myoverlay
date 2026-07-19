"""The ONLY bridge from the GUI to the backend: it shells out to `mt` and
parses JSON. All pipeline logic stays behind the CLI - the GUI never imports
render/ingest/telemetry internals.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable


def mt_base() -> list[str]:
    """The command prefix that invokes `mt`.

    A MYOVERLAY_MT override wins (handy in tests/dev). In the frozen exe the
    launcher forwards argv, so `sys.executable <subcommand>` works. Otherwise
    run the module from the dev tree.
    """
    override = os.environ.get("MYOVERLAY_MT")
    if override:
        return override.split()
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "media_tools.cli"]


def run_json(args: list[str]) -> dict:
    proc = subprocess.run(mt_base() + args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"mt {' '.join(args)} failed")
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(f"mt {' '.join(args)} produced no JSON")
    return json.loads(lines[-1])  # last non-empty line is the JSON payload


def scan() -> dict:
    """Gate-1 data: new content correlated by video + orphan telemetry."""
    return run_json(["scan", "--json"])


def build_plan(day: str) -> dict:
    """Gate-2 data: the default render-plan queue for a day."""
    return run_json(["plan", day, "--json"])


def run_stream(args: list[str], on_line: Callable[[str], None]) -> int:
    """Run a long `mt` command, delivering each stdout line to on_line."""
    proc = subprocess.Popen(
        mt_base() + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip())
    proc.wait()
    return proc.returncode


def in_background(fn: Callable[[], object], done: Callable[[str, object], None], root) -> None:
    """Run fn() off the Tk thread; deliver ("ok", result) or ("err", exc) to
    `done` on the Tk thread via root.after polling."""
    q: queue.Queue = queue.Queue()

    def work():
        try:
            q.put(("ok", fn()))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            q.put(("err", exc))

    threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            status, value = q.get_nowait()
        except queue.Empty:
            root.after(100, poll)
            return
        done(status, value)

    root.after(100, poll)


def write_plan(plan: dict) -> Path:
    """Serialize the edited plan to a temp file that `mt render --plan` reads.
    The format is the CLI's own RenderPlan JSON contract - the GUI only fills in
    the fields, it does not own the schema."""
    import tempfile

    fd, path = tempfile.mkstemp(prefix="render_plan_", suffix=".json")
    os.close(fd)
    Path(path).write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return Path(path)
