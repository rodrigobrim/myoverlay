"""Gate 1: review new content (correlated by video) before ingesting."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class Gate1View(ttk.Frame):
    def __init__(self, master, scan: dict, on_confirm: Callable[[], None]):
        super().__init__(master, padding=10)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Label(
            self, font=("", 12, "bold"),
            text=f"New content to ingest  ({scan.get('date_guess') or 'unknown day'})",
        )
        header.grid(row=0, column=0, sticky="w", pady=(0, 6))

        tree = ttk.Treeview(self, columns=("info",), show="tree headings", height=14)
        tree.heading("#0", text="file")
        tree.heading("info", text="details")
        tree.column("info", width=320)
        tree.grid(row=1, column=0, sticky="nsew")

        for group in scan.get("video_groups", []):
            v = group["video"]
            dur = f"{v['duration_s']:.0f}s" if v.get("duration_s") else "?"
            node = tree.insert("", "end", text=v["source_name"], values=(f"video, {dur}",), open=True)
            if not group.get("telemetry"):
                tree.insert(node, "end", text="(no telemetry)", values=("",))
            for t in group.get("telemetry", []):
                tree.insert(node, "end", text=t["source_name"],
                            values=(f"{t['lap_count']} laps, best {t['best_lap']}",))

        orphans = scan.get("orphan_telemetry", [])
        if orphans:
            onode = tree.insert("", "end", text="orphan telemetry",
                                values=("committed on ingest, no video",), open=True)
            for t in orphans:
                tree.insert(onode, "end", text=t["source_name"],
                            values=(f"{t['lap_count']} laps, best {t['best_lap']}",))

        if not scan.get("video_groups") and not orphans:
            tree.insert("", "end", text="nothing new to ingest", values=("",))

        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="e", pady=(8, 0))
        ttk.Button(bar, text="Confirm & Ingest", command=on_confirm).grid(row=0, column=0)
