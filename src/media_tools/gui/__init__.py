"""Desktop review GUI (Tkinter).

A thin consumer of the `mt` command-line capabilities: it spawns `mt ... --json`
and drives the plan-file handshake (mtclient), and holds no pipeline logic of
its own. See app.main() for the Gate 1 -> ingest -> Gate 2 -> render flow.
"""

from .app import main

__all__ = ["main"]
