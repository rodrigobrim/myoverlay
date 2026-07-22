"""Locate the bundled binaries (ffmpeg, ffprobe, gcloud) by full path.

The frozen exe ships its own ffmpeg and (via the MSI) gcloud, but the pipeline
used to invoke them by bare name and rely on the launcher prepending their
directories to PATH. On a machine with several ffmpeg/gcloud installs that is
fragile - the first one on PATH wins. These helpers resolve a full path
instead, in this order:

  1. an env var the launcher exports from the exe's real location
     (MYOVERLAY_FFMPEG_DIR, MYOVERLAY_GCLOUD_BIN) - authoritative at runtime;
  2. the directory the frozen exe is running from - ground truth on every
     install, whatever the launcher build;
  3. the install directory persisted in config.toml ([tools] install_dir),
     mapped onto the known PyInstaller onedir layout;
  4. the bare name - dev checkouts, zip deploys, or a deleted install, where
     PATH resolution (or the launcher's PATH prepend) still applies.

Step 2 exists because the launcher (which sets the env vars and writes
install_dir) is frozen into the exe, while this module updates with every git
pull: an exe built before those were added would otherwise leave gcloud
unresolvable forever - exactly how `mt google-setup` came to report
"gcloud not found" on a machine where the MSI had installed the SDK next to
the exe. sys.executable needs no cooperation from the launcher, so new code
running under an old exe still finds the bundled tools.

Every full-path candidate is existence-checked, so a config pointing at a
moved/deleted install quietly degrades to the bare name rather than failing.
"""

from __future__ import annotations

import os
import shutil
import sys
from functools import cache
from pathlib import Path


@cache
def _config_install_dir() -> Path | None:
    """The install dir recorded in config.toml, or None if unavailable.

    Loaded lazily and defensively: a missing/invalid config (dev checkout, no
    [tools] section) must never raise - the caller falls back to bare names.
    """
    try:
        from .config import load_config

        install_dir = load_config().tools.install_dir
    except Exception:  # noqa: BLE001 - any config error -> no install dir
        return None
    return Path(install_dir) if install_dir else None


def _frozen_install_dir() -> Path | None:
    """The directory the frozen exe runs from, or None outside a frozen app.

    PyInstaller sets sys.frozen/sys.executable in every build, so this works
    under any launcher version - including ones predating the env vars and the
    config [tools] section - and stays correct if the install is moved.
    """
    if not getattr(sys, "frozen", False):
        return None
    try:
        return Path(sys.executable).resolve().parent
    except OSError:
        return None


def _install_dirs() -> list[Path]:
    """Install-dir candidates, best first (runtime location, then config)."""
    dirs: list[Path] = []
    for candidate in (_frozen_install_dir(), _config_install_dir()):
        if candidate is not None and candidate not in dirs:
            dirs.append(candidate)
    return dirs


def _ffmpeg_dir_from_install(install_dir: Path) -> Path:
    """ffmpeg lives under <install>\\_internal\\ffmpeg in the onedir bundle
    (PyInstaller stages datas into _internal; see myoverlay.spec)."""
    return install_dir / "_internal" / "ffmpeg"


def _gcloud_bin_from_install(install_dir: Path) -> Path:
    """The MSI installs the Google Cloud SDK next to the exe, not inside the
    frozen bundle."""
    return install_dir / "google-cloud-sdk" / "bin"


def _resolve_exe(name: str, env_dir: str, install_subdir) -> str:
    """Full path to `name`.exe if a bundled copy exists, else the bare name."""
    exe = f"{name}.exe" if os.name == "nt" else name

    env = os.environ.get(env_dir)
    if env:
        candidate = Path(env) / exe
        if candidate.is_file():
            return str(candidate)

    for install_dir in _install_dirs():
        candidate = install_subdir(install_dir) / exe
        if candidate.is_file():
            return str(candidate)

    return name


def ffmpeg_exe() -> str:
    return _resolve_exe("ffmpeg", "MYOVERLAY_FFMPEG_DIR", _ffmpeg_dir_from_install)


def ffprobe_exe() -> str:
    return _resolve_exe("ffprobe", "MYOVERLAY_FFMPEG_DIR", _ffmpeg_dir_from_install)


def _gcloud_path() -> str | None:
    """Full path to the bundled gcloud launcher (.cmd on Windows), or None."""
    name = "gcloud.cmd" if os.name == "nt" else "gcloud"

    env = os.environ.get("MYOVERLAY_GCLOUD_BIN")
    if env:
        candidate = Path(env) / name
        if candidate.is_file():
            return str(candidate)

    for install_dir in _install_dirs():
        candidate = _gcloud_bin_from_install(install_dir) / name
        if candidate.is_file():
            return str(candidate)

    return None


def gcloud_cmd() -> list[str]:
    """argv prefix for invoking gcloud.

    gcloud is a .cmd batch file on Windows, which subprocess cannot exec
    directly - it must go through `cmd /c`. Returns the resolved full path when
    a bundled copy exists, else the bare name (found via PATH)."""
    gcloud = _gcloud_path() or "gcloud"
    if os.name == "nt":
        return ["cmd", "/c", gcloud]
    return [gcloud]


def gcloud_available() -> bool:
    """True when gcloud can be invoked - a bundled copy resolved, or one is on
    PATH."""
    return _gcloud_path() is not None or shutil.which("gcloud") is not None


def _reset_cache() -> None:
    """Clear memoized lookups (config may change between test cases)."""
    clear = getattr(_config_install_dir, "cache_clear", None)
    if clear is not None:
        clear()
