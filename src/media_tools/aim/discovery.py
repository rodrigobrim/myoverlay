"""Finding an AiM logger, and getting onto its network.

This sits below the instrument classes, in the part of AiM's stack that is
shared across devices (CConnessione / CInterfacciaRete), so it should hold
for models beyond the MyChron6 even though that is all we have tested.

Discovery is UDP 36002: the probe is the literal ASCII 'aim-ka' and the
device answers with a descriptor carrying its name, IP and serial.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
import time
from typing import NamedTuple

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

_PROFILE_XML_WPA2 = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{profile}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security>
    <authEncryption>
      <authentication>WPA2PSK</authentication>
      <encryption>AES</encryption>
      <useOneX>false</useOneX>
    </authEncryption>
    <sharedKey>
      <keyType>passPhrase</keyType>
      <protected>false</protected>
      <keyMaterial>{password}</keyMaterial>
    </sharedKey>
  </security></MSM>
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


# WinRT WiFiAdapter.ScanAsync, reached through PowerShell. The AsTask
# overloads are picked by parameter type because ScanAsync returns an
# IAsyncAction while the other two return IAsyncOperations.
_SCAN_PS = """
$null = [Windows.Devices.WiFi.WiFiAdapter, Windows.Devices.WiFi, ContentType=WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$m = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$op = ($m | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$act = ($m | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
$null = $op.MakeGenericMethod([Windows.Devices.WiFi.WiFiAccessStatus]).Invoke($null, @([Windows.Devices.WiFi.WiFiAdapter]::RequestAccessAsync())).Result
$adapters = $op.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.WiFi.WiFiAdapter]]).Invoke($null, @([Windows.Devices.WiFi.WiFiAdapter]::FindAllAdaptersAsync())).Result
foreach ($a in $adapters) { $null = $act.Invoke($null, @($a.ScanAsync())).Wait(15000) }
"""


def _force_scan(timeout: float = 30.0) -> None:
    """Make the adapter scan now; netsh only ever reads a cache.

    `netsh wlan show networks` reports the previous scan's results, and
    while Windows is associated to an AP it rescans so rarely that a logger
    powered on right next to the machine can stay invisible indefinitely -
    observed as a cache holding nothing but the connected network. WinRT's
    WiFiAdapter is the one documented way to request a scan on demand, and
    PowerShell is how to reach it without adding a package dependency.

    Best-effort by design: whatever goes wrong (no PowerShell, no adapter,
    location consent denied), the caller still reads the cache as before.
    """
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                        "-Command", _SCAN_PS],
                       capture_output=True, text=True, timeout=timeout)
    except Exception:
        pass


class LoggerAp(NamedTuple):
    """A broadcasting AiM logger: its SSID, and whether it needs a password."""

    ssid: str
    protected: bool


# 'SSID 3 : AiM-MYC6-021763-...' opens a network's block. The number and the
# colon are what distinguish it from the indented 'BSSID 1 :' rows inside it.
_SSID_HEADER = re.compile(r"^SSID\s+\d+\s*:\s*(.*)$")


def find_logger_aps() -> list[LoggerAp]:
    """Every broadcasting AiM logger in range, in the order netsh scans them.

    A fresh scan is forced first - see _force_scan for why netsh alone is
    not enough. A single `mode=bssid` listing then carries both the names
    and the security of each network, so this is one netsh call rather than
    one per logger. netsh localizes the 'Authentication' label, so each
    block is scanned for the untranslated scheme names (WPA*, 802.1X)
    instead of by field name.
    """
    _force_scan()
    try:
        out = _netsh("show", "networks", "mode=bssid").stdout
    except Exception:
        return []
    aps: list[LoggerAp] = []
    current = None      # index of the AiM block being read, if any
    for line in out.splitlines():
        header = _SSID_HEADER.match(line.strip())
        if header:
            ssid = header.group(1).strip()
            current = len(aps) if ssid.startswith("AiM-") else None
            if current is not None:
                aps.append(LoggerAp(ssid, False))
        elif current is not None and ("WPA" in line or "802.1X" in line):
            aps[current] = aps[current]._replace(protected=True)
    return aps


def join_logger_ap(ssid: str, password: str | None = None,
                   timeout: float = 30.0) -> bool:
    """Join the logger's access point, as AiM's own software does.

    Open profile without a password, WPA2-PSK with one. Any profile the AiM
    software left behind is deliberately not reused: on this device it
    specifies WPA2 while the access point is open, and that mismatch makes
    the connection fail silently. The saved profile is never modified or
    deleted.

    Readiness is confirmed with an actual aim-ka reply rather than netsh's
    own status, which lags and has been observed reporting stale state.
    """
    from xml.sax.saxutils import escape

    if password:
        xml = _PROFILE_XML_WPA2.format(profile=TEMP_PROFILE, ssid=escape(ssid),
                                       password=escape(password))
    else:
        xml = _PROFILE_XML.format(profile=TEMP_PROFILE, ssid=escape(ssid))
    path = os.path.join(tempfile.gettempdir(), "aim-auto.xml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    try:
        _netsh("add", "profile", f"filename={path}")
        _netsh("connect", f"name={TEMP_PROFILE}")
    except Exception:
        return False
    finally:
        # The profile XML holds the passphrase in the clear; don't leave it
        # in temp (netsh keeps its own copy in the protected profile store).
        try:
            os.remove(path)
        except OSError:
            pass
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
