"""Review flow controller: Gate 1 (scan -> confirm -> ingest) then Gate 2
(plan -> edit -> render). Every backend step is an `mt` call run off the Tk
thread via mtclient; this module contains no pipeline logic."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import mtclient
from .gate1_view import Gate1View
from .gate2_view import Gate2View


class ReviewApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("media-tools review")
        self.geometry("820x600")
        self.status = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", padx=10, pady=4)
        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)
        self._day: str | None = None
        self.after(150, self._start)

    # ---- helpers ----
    def _clear(self):
        for child in self.container.winfo_children():
            child.destroy()

    def _busy(self, message: str) -> tk.Text:
        self._clear()
        self.status.set(message)
        bar = ttk.Progressbar(self.container, mode="indeterminate")
        bar.pack(fill="x", padx=20, pady=10)
        bar.start(12)
        log = tk.Text(self.container, height=18)
        log.pack(fill="both", expand=True, padx=10, pady=6)
        return log

    def _error(self, message: str, exc: object):
        self._clear()
        self.status.set(message)
        ttk.Label(self.container, text=str(exc), foreground="red", wraplength=780,
                  justify="left").pack(anchor="w", padx=12, pady=12)
        ttk.Button(self.container, text="Retry", command=self._start).pack(anchor="w", padx=12)

    def _stream_to(self, log: tk.Text):
        def on_line(line: str):
            self.after(0, lambda: (log.insert("end", line + "\n"), log.see("end")))
        return on_line

    # ---- Gate 1 ----
    def _start(self):
        self._busy("scanning for new content…")
        mtclient.in_background(mtclient.scan, self._scanned, self)

    def _scanned(self, status, value):
        if status == "err":
            return self._error("scan failed", value)
        self._day = value.get("date_guess")
        self._clear()
        self.status.set("Gate 1 — review new content, then ingest")
        Gate1View(self.container, value, on_confirm=self._ingest).pack(fill="both", expand=True)

    def _ingest(self):
        log = self._busy("ingesting…")
        on_line = self._stream_to(log)
        mtclient.in_background(
            lambda: mtclient.run_stream(["ingest", "--source", "all"], on_line),
            self._ingested, self,
        )

    def _ingested(self, status, value):
        if status == "err":
            return self._error("ingest failed", value)
        if not self._day:
            return self._error("no day to plan", "scan found no dated content")
        self._busy("building render plan…")
        mtclient.in_background(lambda: mtclient.build_plan(self._day), self._planned, self)

    # ---- Gate 2 ----
    def _planned(self, status, value):
        if status == "err":
            return self._error("plan failed", value)
        self._clear()
        self.status.set("Gate 2 — edit each render item, then render")
        Gate2View(self.container, value, on_confirm=self._render).pack(fill="both", expand=True)

    def _render(self, plan: dict):
        log = self._busy("rendering…")
        on_line = self._stream_to(log)
        path = mtclient.write_plan(plan)

        def work():
            code = mtclient.run_stream(["render", plan["date"], "--plan", str(path)], on_line)
            return "rendered" if code == 0 else f"render exited {code}"

        mtclient.in_background(work, lambda st, v: self.status.set(str(v)), self)


def main():
    ReviewApp().mainloop()
