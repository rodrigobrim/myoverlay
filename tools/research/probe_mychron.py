"""Passive + active probing of a MyChron on the network.

Run this once the device is reachable over WiFi. It answers the two questions
static analysis cannot: which UDP port the device announces itself on, and
which TCP port it serves files from.

Read-only: it listens, and it opens TCP connections without sending anything.
It never writes to the device.

    python probe_mychron.py listen           # watch for AiM UDP discovery/keepalive
    python probe_mychron.py scan 192.168.4.1 # find the device's open TCP ports
"""

from __future__ import annotations

import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor

# RS3 sweeps the whole /24 with UDP probes and listens for replies. We don't
# know the port, so bind a range of plausible ones and report anything that
# looks like it came from an AiM device.
LISTEN_PORTS = list(range(4000, 4100)) + [5000, 5001, 8000, 8080, 9000, 9999]


def _looks_like_aim(payload: bytes) -> bool:
    """AiM device names and the IDENTITISSIMA reply carry printable markers."""
    lowered = payload.lower()
    return any(marker in lowered for marker in (b"aim", b"mychron", b"brim"))


def listen(duration_s: float = 120.0) -> None:
    """Bind a spread of UDP ports and dump anything that arrives."""
    socks = []
    for port in LISTEN_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            s.close()  # already in use -- RS3 may hold it
            continue
        s.setblocking(False)
        socks.append(s)

    print(f"listening on {len(socks)} UDP ports for {duration_s:.0f}s ...")
    print("(power-cycle the device's WiFi to catch its announcement)\n")

    deadline = time.monotonic() + duration_s
    seen: set[tuple[str, int, bytes]] = set()

    while time.monotonic() < deadline:
        import select

        ready, _, _ = select.select(socks, [], [], 1.0)
        for s in ready:
            try:
                payload, addr = s.recvfrom(65535)
            except OSError:
                continue
            port = s.getsockname()[1]
            key = (addr[0], port, payload[:32])
            if key in seen:
                continue
            seen.add(key)
            flag = "  <-- AiM?" if _looks_like_aim(payload) else ""
            print(f"udp {addr[0]}:{addr[1]} -> :{port}  {len(payload)}B{flag}")
            print(f"    {payload[:96].hex(' ')}")
            printable = "".join(
                chr(b) if 32 <= b < 127 else "." for b in payload[:96]
            )
            print(f"    {printable}\n")

    for s in socks:
        s.close()
    print(f"done. {len(seen)} distinct datagrams.")


def scan(host: str, start: int = 1, end: int = 65535) -> None:
    """Find open TCP ports on the device. Connect only -- send nothing."""
    print(f"scanning {host} tcp/{start}-{end} ...")
    open_ports: list[int] = []

    def probe(port: int) -> int | None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        try:
            if s.connect_ex((host, port)) == 0:
                return port
        except OSError:
            pass
        finally:
            s.close()
        return None

    with ThreadPoolExecutor(max_workers=256) as pool:
        for result in pool.map(probe, range(start, end + 1)):
            if result is not None:
                open_ports.append(result)
                print(f"  open: tcp/{result}")

    print(f"\n{len(open_ports)} open port(s): {open_ports}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    if sys.argv[1] == "listen":
        listen(float(sys.argv[2]) if len(sys.argv) > 2 else 120.0)
    elif sys.argv[1] == "scan":
        if len(sys.argv) < 3:
            raise SystemExit("scan needs the device IP")
        scan(sys.argv[2])
    else:
        raise SystemExit(f"unknown command: {sys.argv[1]}")
