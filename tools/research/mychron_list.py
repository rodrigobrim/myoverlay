"""List recorded sessions on an AiM MyChron6 directly over WiFi.

No Race Studio, no GUI automation. The PC must be joined to the logger's
access point (it is the DHCP server and gateway, typically 10.0.0.1).

Wire format
-----------
    header : b'<h' + TAG(4) + u32 body_len + u8 flag + b'>'      12 bytes
    body   : body_len bytes
    trailer: b'<'  + TAG(4) + u16 checksum + b'>'                 8 bytes
             checksum = sum(body) & 0xFFFF

    TAG 'STNC' = command, 'STCP' = response/control.
    Command body is 64 bytes: opcode at +8, argument string at +32.

Per-command exchange
--------------------
    C->D <STNC> 64   command
    D->C <STCP> 64   echo
    C->D <STCP> 68   client timestamp frame
    D->C <STCP>  4   ack
    D->C <STCP> 64   result header
    C->D <STCP>  4   ack
    D->C <STCP>  N   payload

Read-only: issues identify and directory-listing opcodes only. It never
sends a write, delete, format or file-transfer command.

ALL columns the device reports are preserved verbatim, with their original
names and original order - including columns that are empty for every row.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import socket
import struct
import sys
import time
from datetime import datetime, timezone

DEFAULT_HOST = "10.0.0.1"
DEFAULT_PORT = 2000

HELLO_BODY = bytes.fromhex("0000000006080000")
ACK4 = b"\x00\x00\x00\x00"

OP_IDENTIFY = 0x00010010
OP_SESSION_LIST = 0x00020024

# Opcodes that mutate the device. Never sent by this module; listed so the
# constants are documented in one place and cannot be reached by accident.
DESTRUCTIVE = {
    0x00040006: "DbgDelFile", 0x00040007: "removeDir",
    0x00040008: "delDirContent", 0x00040009: "DbgFormatMedia",
    0x0004000D: "DbgFormatDati", 0x00051000: "eraseFirmware",
    0x000C0005: "DbgResetWiFi",
}


# ---------------------------------------------------------------- framing
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
    captured RS3 exchange they were 14:38 at +12 and 11:38 at +44 - exactly
    the UTC-3 offset of the machine that produced it. So +12 is UTC and +44
    is local. Writing local time into both (as an earlier version did) puts
    a wrong UTC in front of a device that may discipline its clock from it.
    """
    utc = datetime.now(timezone.utc)
    local = datetime.now()
    body = bytearray(68)
    for base, t in ((12, utc), (44, local)):
        struct.pack_into("<IIIII", body, base,
                         t.year, t.month, t.day, t.hour, t.minute)
    return bytes(body)


class MyChron:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 10.0):
        if any(op in DESTRUCTIVE for op in ()):  # documentation guard
            raise AssertionError
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""
        self.timeout = timeout

    # ------------------------------------------------------------ plumbing
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

    def hello(self) -> bytes | None:
        self._send(b"STCP", HELLO_BODY)
        reply = self._read_frame()
        return reply[2] if reply else None

    def run_command(self, opcode: int, arg: bytes = b"", f16: int = 0) -> bytes:
        """Drive one command through the full handshake; return its payload."""
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

    def session_list_raw(self) -> bytes:
        """Raw payload: 4-byte status word followed by CSV."""
        self.run_command(OP_IDENTIFY, f16=0x40)
        return self.run_command(OP_SESSION_LIST)

    def close(self) -> None:
        self.sock.close()


# ---------------------------------------------------------------- parsing
def parse_sessions(payload: bytes):
    """(columns, rows) preserving every column, name and order verbatim.

    `columns` is the device's header line as-is, including any unnamed
    trailing field. `rows` are lists of raw strings, padded to len(columns)
    so no field is ever dropped.
    """
    text = payload[4:].decode("latin-1")
    parsed = list(csv.reader(io.StringIO(text)))
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


def render_table(columns, rows) -> str:
    widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
              for i, c in enumerate(columns)]
    out = [" | ".join(c.ljust(w) for c, w in zip(columns, widths)),
           "-+-".join("-" * w for w in widths)]
    for row in rows:
        out.append(" | ".join(v.ljust(w) for v, w in zip(row, widths)))
    return "\n".join(out)


# ------------------------------------------------------------------- cli
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--format", choices=("table", "csv", "json"), default="table")
    ap.add_argument("--out", help="write to this file instead of stdout")
    ap.add_argument("--raw", help="also save the untouched wire payload here")
    args = ap.parse_args(argv)

    dev = MyChron(args.host, args.port)
    try:
        if dev.hello() is None:
            print(f"no response from {args.host}:{args.port}", file=sys.stderr)
            return 1
        payload = dev.session_list_raw()
    finally:
        dev.close()

    if not payload:
        print("empty session list", file=sys.stderr)
        return 1
    if args.raw:
        with open(args.raw, "wb") as fh:
            fh.write(payload)

    columns, rows = parse_sessions(payload)

    if args.format == "csv":
        # verbatim passthrough - byte-identical to what the device sent
        text = payload[4:].decode("latin-1")
    elif args.format == "json":
        text = json.dumps(rows_as_dicts(columns, rows), indent=2)
    else:
        text = render_table(columns, rows)

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"{len(rows)} sessions, {len(columns)} columns -> {args.out}")
    else:
        print(text)
        print(f"\n{len(rows)} sessions, {len(columns)} columns", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
