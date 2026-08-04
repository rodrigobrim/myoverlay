"""Single entry point for decoding AiM .xrk files via libxrk.

libxrk's decoder prints raw channel-parsing chatter ("Unknown units[..]")
straight to stdout. It is diagnostic noise, not user-facing, so every decode
in the app routes through here and the chatter is swallowed unless the
media_tools logger is at DEBUG (CLI --verbosity=debug).
"""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path


def load_xrk(path: Path):
    """Decode an .xrk into a libxrk LogFile, suppressing decoder chatter."""
    from libxrk import aim_xrk  # deferred: import builds Cython state

    if logging.getLogger("media_tools").isEnabledFor(logging.DEBUG):
        return aim_xrk(str(path))
    with contextlib.redirect_stdout(io.StringIO()):
        return aim_xrk(str(path))
