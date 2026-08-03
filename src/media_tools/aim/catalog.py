"""Parsing for the session listing AiM 2G loggers return.

The device answers leggiSummaryFilesRegistrati with CSV: a header row naming
every column, then one row per recorded session. Every column is preserved
verbatim - names, order, and the ones that are empty on every row - because
which fields a given model populates is not ours to decide.
"""

from __future__ import annotations

import csv
import io


def parse_sessions(csv_text: str):
    """(columns, rows) with nothing dropped.

    `columns` is the device's header line as-is, including any unnamed
    trailing field. `rows` are lists of raw strings, padded to len(columns)
    so a short row never silently loses its tail.
    """
    parsed = list(csv.reader(io.StringIO(csv_text)))
    if not parsed:
        return [], []
    columns = parsed[0]
    rows = []
    for row in parsed[1:]:
        if not row or not row[0].strip():
            continue
        if len(row) < len(columns):
            row = row + [""] * (len(columns) - len(row))
        rows.append(row)
    return columns, rows


def rows_as_dicts(columns, rows):
    """One dict per session, keys exactly as the device names them."""
    return [dict(zip(columns, row)) for row in rows]


def as_catalog(csv_text: str):
    """(columns, rows, {name: row_dict}) - the usual three views at once."""
    columns, rows = parse_sessions(csv_text)
    return columns, rows, {r["name"]: r for r in rows_as_dicts(columns, rows)}


def render_table(columns, rows) -> str:
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
              for i, c in enumerate(columns)]
    out = [" | ".join(c.ljust(w) for c, w in zip(columns, widths)),
           "-+-".join("-" * w for w in widths)]
    for row in rows:
        out.append(" | ".join(v.ljust(w) for v, w in zip(row, widths)))
    return "\n".join(out)


def strip_status_word(payload: bytes) -> str:
    """CSV text from a raw payload.

    The TCP transport prefixes the listing with a 4-byte status word; the USB
    one does not. Detect rather than assume, so either can feed this.
    """
    text = payload.decode("latin-1")
    return text if text.startswith("name,") else payload[4:].decode("latin-1")
