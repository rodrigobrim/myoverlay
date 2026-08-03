"""Finding an AiM logger, and getting onto its network.

This sits below the instrument classes, in the part of AiM's stack that is
shared across devices (CConnessione / CInterfacciaRete), so it should hold
for models beyond the MyChron6 even though that is all we have tested.

Discovery is UDP 36002: the probe is the literal ASCII 'aim-ka' and the
device answers with a descriptor carrying its name, IP and serial.
"""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time

WIFI_HOST = "10.0.0.1"          # the logger is its own DHCP server and gateway
WIFI_UDP_PORT = 36002
PROBE = b"aim-ka"

TEMP_PROFILE = "AiM-MyChron-auto"

_PROFILE_XML = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{profile}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security><authEncryption>
    <authentication>open</authentication>
    <encryption>none</encryption>
    <useOneX>false</useOneX>
  </authEncryption></security></MSM>
</WLANProfile>
"""


def probe(host: str = WIFI_HOST, timeout: float = 1.5) -> bytes | None:
    """Send one aim-ka; return the device descriptor, or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(PROBE, (host, WIFI_UDP_PORT))
        data, _addr = s.recvfrom(4096)
        return data or None
    except OSError:
        return None
    finally:
        s.close()


def wifi_available(timeout: float = 1.5) -> bool:
    return probe(timeout=timeout) is not None


def _netsh(*args):
    return subprocess.run(["netsh", "wlan", *args],
                          capture_output=True, text=True, timeout=30)


def find_logger_ap() -> str | None:
    """SSID of a broadcasting AiM logger, if one is in range."""
    try:
        out = _netsh("show", "networks").stdout
    except Exception:
        return None
    for line in out.splitlines():
        if "AiM-" in line and ":" in line:
            ssid = line.split(":", 1)[1].strip()
            if ssid.startswith("AiM-"):
                return ssid
    return None


def join_logger_ap(ssid: str, timeout: float = 30.0) -> bool:
    """Join the logger's access point, as Race Studio does.

    Uses a throwaway OPEN profile. Any profile the AiM software left behind
    is deliberately not reused: on this device it specifies WPA2 while the
    access point is open, and that mismatch makes the connection fail
    silently. The saved profile is never modified or deleted.

    Readiness is confirmed with an actual aim-ka reply rather than netsh's
    own status, which lags and has been observed reporting stale state.
    """
    xml = _PROFILE_XML.format(profile=TEMP_PROFILE, ssid=ssid)
    path = os.path.join(tempfile.gettempdir(), "aim-auto.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    try:
        _netsh("add", "profile", f"filename={path}")
        _netsh("connect", f"name={TEMP_PROFILE}")
    except Exception:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if wifi_available(timeout=1.0):
            return True
        time.sleep(1.0)
    return False


def leave_logger_ap() -> None:
    """Drop the link and remove the throwaway profile."""
    try:
        _netsh("disconnect")
        _netsh("delete", "profile", f"name={TEMP_PROFILE}")
    except Exception:
        pass
