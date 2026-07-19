"""Gate 2: the render-plan queue with a per-item editor.

Edits are scoped to one item (1 item == 1 rendered output). The view owns the
plan dict (the CLI's RenderPlan JSON) and mutates it in place; it holds no
pipeline logic - best lap, defaults, join feasibility all come from `mt`.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
from typing import Callable

from .widgets import CheckedEntry, ReorderableList

QUALITIES = ["hd", "fhd", "2k", "4k"]


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _parse_time(text: str) -> float:
    text = text.strip()
    if not text:
        return 0.0
    if ":" in text:
        m, s = text.split(":", 1)
        return int(m) * 60 + float(s)
    return float(text)


class Gate2View(ttk.Frame):
    def __init__(self, master, plan: dict, on_confirm: Callable[[dict], None], raw_dir: Path | None = None):
        super().__init__(master, padding=10)
        self.plan = plan
        self.on_confirm = on_confirm
        self.raw_dir = Path(raw_dir) if raw_dir else None
        self._current: int | None = None
        self._widgets: dict = {}

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ttk.Frame(self)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        ttk.Label(left, text="render items", font=("", 10, "bold")).pack(anchor="w")
        self.items_box = tk.Listbox(left, height=16, width=32, exportselection=False)
        self.items_box.pack(fill="y", expand=True)
        for it in plan.get("items", []):
            self.items_box.insert("end", it["item_id"])
        self.items_box.bind("<<ListboxSelect>>", self._on_select)

        self.editor = ttk.Frame(self)
        self.editor.grid(row=0, column=1, sticky="nsew")

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(bar, text="Confirm queue → Render", command=self._confirm).grid(row=0, column=0)

        if plan.get("items"):
            self.items_box.selection_set(0)
            self._show(0)

    # ---- selection ----
    def _on_select(self, _event=None):
        sel = self.items_box.curselection()
        if sel:
            self._show(sel[0])

    def _show(self, index: int):
        if self._current is not None:
            self._collect(self._current)
        self._current = index
        for child in self.editor.winfo_children():
            child.destroy()
        self._build_editor(self.plan["items"][index])

    # ---- editor ----
    def _build_editor(self, item: dict):
        e = self.editor
        e.columnconfigure(1, weight=1)
        row = 0

        ttk.Label(e, text=f"best lap:  {item.get('best_lap', '-:--.--')}",
                  font=("", 10, "bold")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        ttk.Label(e, text="video slices (join order)").grid(row=row, column=0, sticky="nw", pady=4)
        slices = ReorderableList(e, [s["source_name"] for s in item.get("slices", [])], on_add=self._add_slice)
        slices.grid(row=row, column=1, sticky="ew", pady=4)
        self._widgets["slices"] = slices
        row += 1

        ttk.Label(e, text="telemetry files").grid(row=row, column=0, sticky="nw", pady=4)
        tel = ReorderableList(e, list(item.get("telemetry_files", [])), on_add=self._add_telemetry, height=3)
        tel.grid(row=row, column=1, sticky="ew", pady=4)
        self._widgets["telemetry"] = tel
        row += 1

        start = CheckedEntry(e, "start at", item.get("start_enabled", False),
                             _fmt_time(item.get("start_s", 0.0)))
        start.grid(row=row, column=1, sticky="w")
        self._widgets["start"] = start
        row += 1
        end = CheckedEntry(e, "end at", item.get("end_enabled", False),
                           _fmt_time(item.get("end_s", 0.0)))
        end.grid(row=row, column=1, sticky="w")
        self._widgets["end"] = end
        row += 1

        ttk.Label(e, text="quality").grid(row=row, column=0, sticky="w", pady=4)
        quality = ttk.Combobox(e, values=QUALITIES, state="readonly", width=8)
        quality.set(item.get("quality", "2k"))
        quality.grid(row=row, column=1, sticky="w")
        self._widgets["quality"] = quality
        row += 1

        ttk.Label(e, text="title").grid(row=row, column=0, sticky="w", pady=4)
        title = ttk.Entry(e)
        title.insert(0, item.get("title", ""))
        title.grid(row=row, column=1, sticky="ew")
        self._widgets["title"] = title
        row += 1

        append = tk.BooleanVar(value=item.get("append_best_lap", True))
        ttk.Checkbutton(e, text="append best lap to title", variable=append).grid(
            row=row, column=1, sticky="w")
        self._widgets["append"] = append
        row += 1

        ttk.Label(e, text="description").grid(row=row, column=0, sticky="nw", pady=4)
        desc = tk.Text(e, height=4, width=40)
        desc.insert("1.0", item.get("description", ""))
        desc.grid(row=row, column=1, sticky="ew")
        self._widgets["description"] = desc

    def _collect(self, index: int):
        item = self.plan["items"][index]
        # slices: reorder/keep by source_name, preserving file paths
        by_name = {s["source_name"]: s for s in item.get("slices", [])}
        by_name.update(getattr(self, "_extra_slices", {}))
        item["slices"] = [by_name.get(n, {"file": n, "source_name": n}) for n in self._widgets["slices"].values()]
        item["telemetry_files"] = self._widgets["telemetry"].values()
        item["start_enabled"], sv = self._widgets["start"].get()
        item["start_s"] = _parse_time(sv)
        item["end_enabled"], ev = self._widgets["end"].get()
        item["end_s"] = _parse_time(ev)
        item["quality"] = self._widgets["quality"].get()
        item["title"] = self._widgets["title"].get()
        item["append_best_lap"] = self._widgets["append"].get()
        item["description"] = self._widgets["description"].get("1.0", "end").strip()

    def _add_slice(self, widget: ReorderableList):
        path = filedialog.askopenfilename(
            title="add video slice",
            initialdir=str(self.raw_dir / "video") if self.raw_dir else None,
            filetypes=[("video", "*.mp4 *.MP4 *.mov")],
        )
        if not path:
            return
        name = Path(path).name
        self.__dict__.setdefault("_extra_slices", {})[name] = {"file": path, "source_name": name}
        widget.add(name)

    def _add_telemetry(self, widget: ReorderableList):
        path = filedialog.askopenfilename(
            title="add telemetry",
            initialdir=str(self.raw_dir / "telemetry") if self.raw_dir else None,
            filetypes=[("telemetry", "*.xrk")],
        )
        if path:
            widget.add(path)

    def _confirm(self):
        if self._current is not None:
            self._collect(self._current)
        self.on_confirm(self.plan)
