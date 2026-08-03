"""MyChron6 over WiFi.

The PC joins the logger's own access point; the logger is the DHCP server
and gateway, and serves a framed protocol on tcp/2000.

Wire format
-----------
    header : b'<h' + TAG(4) + u32 body_len + u8 flag + b'>'      12 bytes
    body   : body_len bytes
    trailer: b'<'  + TAG(4) + u16 checksum + b'>'                 8 bytes
             checksum = sum(body) & 0xFFFF

    TAG 'STNC' carries commands, 'STCP' responses and control.
    Command body is 64 bytes: opcode at +8, path argument at +32.

The ASCII tagging is why plain length-prefixed guesses get nowhere: without
the '<h' marker the device never sees a frame start, so it accepts the
connection and waits indefinitely rather than resetting.

Per-command exchange
--------------------
    C->D <STNC> 64   command
    D->C <STCP> 64   echo
    C->D <STCP> 68   client timestamp frame   <- device stalls without it
    D->C <STCP>  4   ack
    D->C <STCP> 64   result header
    C->D <STCP>  4   ack
    D->C <STCP>  N   payload
"""

from __future__ import annotations

import socket
import struct
import sys
import time
from datetime import datetime, timezone

from ... import discovery
from ...catalog import strip_status_word

DEFAULT_HOST = discovery.WIFI_HOST
DEFAULT_PORT = 2000

HELLO_BODY = bytes.fromhex("0000000006080000")
ACK4 = b"\x00\x00\x00\x00"

OP_IDENTIFY = 0x00010010
OP_SESSION_LIST = 0x00020024
OP_GET_FILE = 0x00040002

SESSION_DIR = "1:/mem/"
MAX_UNANNOUNCED = 128 * 1024 * 1024   # refuse to stream forever if size is lost

# Opcodes that mutate the device. Never sent; named so a mistyped constant
# cannot reach the logger.
DESTRUCTIVE = {
    0x00040006: "DbgDelFile", 0x00040007: "removeDir",
    0x00040008: "delDirContent", 0x00040009: "DbgFormatMedia",
    0x0004000D: "DbgFormatDati", 0x00051000: "eraseFirmware",
    0x000C0005: "DbgResetWiFi",
}


def build_frame(tag: bytes, body: bytes, flag: int = 0) -> bytes:
    header = b"<h" + tag + struct.pack("<IB", len(body), flag) + b">"
    trailer = b"<" + tag + struct.pack("<H", sum(body) & 0xFFFF) + b">"
    return header + body + trailer


def command_body(opcode: int, arg: bytes = b"", f16: int = 0, f24: int = 1) -> bytes:
    body = bytearray(64)
    struct.pack_into("<I", body, 8, opcode)
    struct.pack_into("<I", body, 16, f16)
    struct.pack_into("<I", body, 24, f24)
    if arg:
        body[32:32 + len(arg) + 1] = arg + b"\x00"
    return bytes(body)


def timestamp_body() -> bytes:
    """68-byte frame carrying the PC clock; the device waits for it.

    It holds TWO y/m/d/h/m records and they are not the same clock. In the
    captured Race Studio exchange they read 14:38 at +12 and 11:38 at +44 -
    exactly the UTC-3 offset of the machine that produced it. So +12 is UTC
    and +44 is local. Writing local time into both puts a wrong UTC in front
    of a device that may discipline its clock from it.
    """
    utc = datetime.now(timezone.utc)
    local = datetime.now()
    body = bytearray(68)
    for base, t in ((12, utc), (44, local)):
        struct.pack_into("<IIIII", body, base,
                         t.year, t.month, t.day, t.hour, t.minute)
    return bytes(body)


class Transport:
    """MyChron6 WiFi transport."""

    kind = "WiFi"

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 10.0, auto_join: bool = True):
        self.joined = False
        if auto_join and not discovery.wifi_available():
            ssid = discovery.find_logger_ap()
            if ssid:
                print(f"joining {ssid} ...", file=sys.stderr)
                if not discovery.join_logger_ap(ssid):
                    raise OSError(f"could not join {ssid}")
                self.joined = True
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        self.timeout = timeout
        if self._hello() is None:
            self.close()
            raise OSError("no hello response")

    # ------------------------------------------------------------- plumbing
    def _send(self, tag: bytes, body: bytes) -> None:
        self.sock.sendall(build_frame(tag, body))

    def _read_frame(self, timeout: float = 6.0):
        deadline = time.monotonic() + timeout
        while True:
            i = self.buf.find(b"<h")
            if i >= 0 and len(self.buf) >= i + 12:
                tag = self.buf[i + 2:i + 6]
                blen, flag = struct.unpack_from("<IB", self.buf, i + 6)
                if (self.buf[i + 11:i + 12] == b">"
                        and len(self.buf) >= i + 12 + blen + 8):
                    body = self.buf[i + 12:i + 12 + blen]
                    self.buf = self.buf[i + 12 + blen + 8:]
                    return tag.decode("ascii", "replace"), flag, body
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(max(0.2, remaining))
            try:
                chunk = self.sock.recv(65535)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self.buf += chunk

    def _hello(self) -> bytes | None:
        self._send(b"STCP", HELLO_BODY)
        reply = self._read_frame()
        return reply[2] if reply else None

    def run_command(self, opcode: int, arg: bytes = b"", f16: int = 0) -> bytes:
        if opcode in DESTRUCTIVE:
            raise ValueError(f"refusing destructive opcode {opcode:#010x}")
        self._send(b"STNC", command_body(opcode, arg, f16=f16))
        sent_timestamp = False
        for _ in range(40):
            frame = self._read_frame()
            if frame is None:
                break
            _tag, _flag, body = frame
            if len(body) == 64:
                if not sent_timestamp:
                    self._send(b"STCP", timestamp_body())
                    sent_timestamp = True
                else:
                    self._send(b"STCP", ACK4)
            elif len(body) == 4:
                continue
            else:
                self._send(b"STCP", ACK4)
                return body
        return b""

    # ---------------------------------------------------------------- public
    def session_csv(self) -> str:
        self.run_command(OP_IDENTIFY, f16=0x40)
        return strip_status_word(self.run_command(OP_SESSION_LIST))

    def fetch(self, name: str, fh, expected: int | None = None,
              progress=None, timeout: float = 30.0) -> int:
        """Stream a session into `fh`; returns bytes written."""
        path = name if (len(name) > 2 and name[0].isdigit()
                        and name[1] == ":") else SESSION_DIR + name.lstrip("/")
        self._send(b"STNC", command_body(OP_GET_FILE, path.encode("latin-1")))

        size = None
        written = 0
        while True:
            frame = self._read_frame(timeout=timeout)
            if frame is None:
                break
            _tag, _flag, body = frame

            if len(body) == 64:
                announced = struct.unpack_from("<I", body, 16)[0]
                # First 64-byte frame is a bare echo (size 0); the second
                # carries the real length and is the one to ack.
                if announced:
                    size = announced
                    self._send(b"STCP", ACK4)
                continue
            if len(body) <= 4:
                continue

            data = body[4:]
            if size is not None:
                data = data[:max(0, size - written)]
            fh.write(data)
            written += len(data)
            if progress and size:
                progress(written, size)

            if size is not None and written >= size:
                break
            if size is None and written > MAX_UNANNOUNCED:
                raise RuntimeError("no size header and the stream keeps going")

            # Not an acknowledgement: a cursor saying how many bytes we hold.
            # The device resumes from there, so sending zero makes it pad the
            # final chunk instead of sending the true remainder.
            self._send(b"STCP", struct.pack("<I", written))

        return written

    def close(self):
        try:
            self.sock.close()
        finally:
            # Only undo what we did; if the machine was already on the
            # logger's network, leave it there.
            if self.joined:
                discovery.leave_logger_ap()
                self.joined = False
