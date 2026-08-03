"""AiM logger clients.

Layout mirrors AiM's own split: everything below the instrument class is
shared (discovery, framing-agnostic helpers), while the command set and
storage layout are per model.

    aim/discovery.py        finding a logger, joining its access point
    aim/catalog.py          parsing the session listing
    aim/mychron/v6/usb.py   MyChron6 over USB
    aim/mychron/v6/wifi.py  MyChron6 over WiFi

Only the MyChron6 is implemented. Other models will need their own package:
RS3 splits devices across CStrumentoMXL2 (2G, has a filesystem) and the
CStrumentoSpansionNoFileSystem family (older, no filesystem, different
download path entirely), so the command layer here is not portable as-is.
"""

from .errors import NoDeviceError
from .connect import connect

__all__ = ["connect", "NoDeviceError"]
