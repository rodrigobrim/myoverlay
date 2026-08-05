"""Per-SSID WiFi passwords, encrypted at rest with Windows DPAPI.

DPAPI (CryptProtectData / CryptUnprotectData in crypt32.dll) encrypts with a
master key Windows derives from the logged-on user's credentials: nothing to
generate or manage, the blob is only readable by the same user on the same
machine, and it works unchanged on Windows 10 and 11. The encrypted blobs are
kept outside the project, in the app data dir next to config.toml
(~/MyOverlay/wifi-credentials.json), base64-encoded and keyed by SSID.
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
from pathlib import Path

STORE_PATH = Path.home() / "MyOverlay" / "wifi-credentials.json"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(func_name: str, data: bytes) -> bytes:
    crypt32 = ctypes.windll.crypt32
    inp = _DATA_BLOB(len(data), ctypes.cast(
        ctypes.create_string_buffer(data, len(data)),
        ctypes.POINTER(ctypes.c_char)))
    out = _DATA_BLOB()
    # NULL entropy/prompt: the user's DPAPI master key alone protects it.
    ok = getattr(crypt32, func_name)(
        ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError(f"{func_name} failed (WinError {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(secret: str) -> bytes:
    return _crypt("CryptProtectData", secret.encode("utf-8"))


def unprotect(blob: bytes) -> str:
    return _crypt("CryptUnprotectData", blob).decode("utf-8")


def _load() -> dict[str, str]:
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(ssid: str) -> str | None:
    """Stored password for `ssid`, or None if absent or undecryptable
    (different user/machine - treat as not stored)."""
    b64 = _load().get(ssid)
    if not b64:
        return None
    try:
        return unprotect(base64.b64decode(b64))
    except (OSError, ValueError):
        return None


def save(ssid: str, password: str) -> None:
    entries = _load()
    entries[ssid] = base64.b64encode(protect(password)).decode("ascii")
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def forget(ssid: str) -> None:
    entries = _load()
    if entries.pop(ssid, None) is not None:
        STORE_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
