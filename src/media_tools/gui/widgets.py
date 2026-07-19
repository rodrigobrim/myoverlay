"""Reusable ttk widgets for the review editor."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class ReorderableList(ttk.Frame):
    """A listbox with Up/Down/Add/Remove - for the ordered video slices and
    the telemetry-file list."""

    def __init__(self, master, items=(), on_add: Callable[["ReorderableList"], None] | None = None, height=5):
        super().__init__(master)
        self.listbox = tk.Listbox(self, height=height, exportselection=False)
        self.listbox.grid(row=0, column=0, rowspan=4, sticky="nsew")
        for it in items:
            self.listbox.insert("end", it)
        ttk.Button(self, text="↑", width=3, command=self.move_up).grid(row=0, column=1, sticky="n")
        ttk.Button(self, text="↓", width=3, command=self.move_down).grid(row=1, column=1)
        ttk.Button(self, text="+", width=3, command=lambda: on_add and on_add(self)).grid(row=2, column=1)
        ttk.Button(self, text="−", width=3, command=self.remove).grid(row=3, column=1, sticky="s")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

    def values(self) -> list[str]:
        return list(self.listbox.get(0, "end"))

    def set_values(self, items) -> None:
        self.listbox.delete(0, "end")
        for it in items:
            self.listbox.insert("end", it)

    def add(self, value: str) -> None:
        self.listbox.insert("end", value)

    def _selected(self) -> int | None:
        sel = self.listbox.curselection()
        return sel[0] if sel else None

    def _swap(self, i: int, j: int) -> None:
        vals = self.values()
        vals[i], vals[j] = vals[j], vals[i]
        self.set_values(vals)
        self.listbox.selection_set(j)

    def move_up(self) -> None:
        i = self._selected()
        if i is not None and i > 0:
            self._swap(i, i - 1)

    def move_down(self) -> None:
        i = self._selected()
        if i is not None and i < self.listbox.size() - 1:
            self._swap(i, i + 1)

    def remove(self) -> None:
        i = self._selected()
        if i is not None:
            self.listbox.delete(i)


class CheckedEntry(ttk.Frame):
    """A checkbox that enables/disables an entry - for the optional start/end
    time toggles."""

    def __init__(self, master, label: str, checked=False, value=""):
        super().__init__(master)
        self.enabled = tk.BooleanVar(value=checked)
        self.value = tk.StringVar(value=value)
        ttk.Checkbutton(self, text=label, variable=self.enabled, command=self._sync).grid(row=0, column=0, sticky="w")
        self.entry = ttk.Entry(self, textvariable=self.value, width=10)
        self.entry.grid(row=0, column=1, padx=4)
        self._sync()

    def _sync(self) -> None:
        self.entry.configure(state="normal" if self.enabled.get() else "disabled")

    def get(self) -> tuple[bool, str]:
        return self.enabled.get(), self.value.get()
