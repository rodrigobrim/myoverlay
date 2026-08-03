"""Pick a transport: USB first, then WiFi, then say so plainly.

USB is preferred because it is roughly three times faster and charges the
logger while it works - over WiFi the radio drains the battery faster than
USB charges it.

Both probes are side-effect free: USB is opening the device interface, WiFi
is a single aim-ka datagram.
"""

from __future__ import annotations

import sys

from .errors import NoDeviceError


def _usb():
    from .mychron.v6.usb import Transport
    return Transport()


def _wifi():
    from .mychron.v6.wifi import Transport
    return Transport()


TRANSPORTS = {"usb": _usb, "wifi": _wifi}
ORDER = ("usb", "wifi")


def connect(prefer: str | None = None, verbose: bool = True):
    """Return a connected transport, or raise NoDeviceError.

    `prefer` forces one transport instead of trying both in order.
    """
    names = (prefer,) if prefer else ORDER
    tried = []
    for name in names:
        try:
            dev = TRANSPORTS[name]()
        except Exception as exc:
            tried.append(f"{name}: {type(exc).__name__}")
            continue
        if verbose:
            print(f"connected over {dev.kind}", file=sys.stderr)
        return dev

    raise NoDeviceError(
        "No MyChron found.\n"
        "  - plug the logger in over USB, or\n"
        "  - turn on its WiFi (the client joins the AiM-... network itself)\n"
        f"  tried -> {', '.join(tried)}"
    )
