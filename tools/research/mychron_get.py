"""mychron_get - fetch recorded sessions from an AiM MyChron6 over WiFi.

Companion to mychron_list.py, which supplies the framing and connection.
The PC must be joined to the logger's access point (device at 10.0.0.1).

Transfer protocol (recovered from a captured RS3 download)
---------------------------------------------------------
    C->D <STNC> 64      getFile (0x00040002), path in the arg field at +32
    D->C <STCP> 64      echo            (size field at +16 is zero)
    D->C <STCP> 64      result header   (size field at +16 = file length)
    C->D <STCP>  4      ack
    D->C <STCP> <=65476 chunk: 4-byte prefix + up to 65472 data bytes
    C->D <STCP>  4      ack
    ... repeat until `size` data bytes have been received

Sessions live at 1:/mem/<name>. Only getFile is ever issued - this module
cannot delete, format or write to the device.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time

from mychron_list import (
    ACK4,
    DESTRUCTIVE,
    MyChron,
    command_body,
    parse_sessions,
    rows_as_dicts,
)

OP_GETFILE = 0x00040002
SESSION_DIR = "1:/mem/"
CHUNK_DATA = 65472          # max data bytes per chunk (frame is this + 4)


def remote_path(name: str) -> str:
    """Accept a bare name ('a_0186.xrz') or a full device path ('1:/mem/...')."""
    if len(name) > 2 and name[0].isdigit() and name[1] == ":":
        return name
    return SESSION_DIR + name.lstrip("/")


MAX_UNANNOUNCED = 128 * 1024 * 1024   # refuse to stream forever if size is lost


class Downloader(MyChron):
    def _drain(self, timeout: float = 1.5) -> int:
        """Discard frames left over from a completed transfer.

        Without this the next getFile starts mid-stream, never sees its
        size header, and streams until the device drops the connection.
        """
        dropped = 0
        while True:
            frame = self._read_frame(timeout=timeout)
            if frame is None:
                return dropped
            dropped += 1

    def fetch(self, name: str, fh, progress=None, timeout: float = 30.0):
        """Stream one file into `fh`. Returns (announced_size, bytes_written)."""
        path = remote_path(name)
        if OP_GETFILE in DESTRUCTIVE:            # belt and braces
            raise AssertionError("getFile must not be a destructive opcode")

        self._send(b"STNC", command_body(OP_GETFILE, path.encode("latin-1")))

        size = None
        written = 0
        started = time.monotonic()

        while True:
            frame = self._read_frame(timeout=timeout)
            if frame is None:
                break
            _tag, _flag, body = frame

            if len(body) == 64:
                announced = struct.unpack_from("<I", body, 16)[0]
                # The first 64-byte frame is a bare echo (size == 0); the
                # second carries the real length and is the one to ack.
                if announced:
                    size = announced
                    self._send(b"STCP", ACK4)
                continue

            if len(body) <= 4:
                continue

            # Payload chunk: 4-byte prefix, then data. The final chunk is
            # short rather than padded. Trim anyway as a guard.
            data = body[4:]
            if size is not None:
                data = data[:max(0, size - written)]
            fh.write(data)
            written += len(data)

            if progress and size:
                progress(written, size, time.monotonic() - started)

            # The 4-byte frame is not an acknowledgement - it is a cursor
            # saying how many bytes we already hold, and the device resumes
            # from there. Sending zero every time leaves it guessing, so it
            # pads the final chunk instead of sending the true remainder.
            if size is not None and written >= size:
                break
            if size is None and written > MAX_UNANNOUNCED:
                raise RuntimeError(
                    "no size header seen and the stream keeps going - "
                    "aborting instead of writing an unbounded file")

            self._send(b"STCP", struct.pack("<I", written))

        return size, written


def _progress(done, total, elapsed):
    pct = 100.0 * done / total if total else 0.0
    rate = done / elapsed / 1024 if elapsed > 0 else 0.0
    sys.stderr.write(f"\r    {done:>9,} / {total:,} bytes  {pct:5.1f}%  "
                     f"{rate:7.0f} KiB/s")
    sys.stderr.flush()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("names", nargs="*", help="session file names, e.g. a_0186.xrz")
    ap.add_argument("--host", default="10.0.0.1")
    ap.add_argument("--port", type=int, default=2000)
    ap.add_argument("--out", default=".", help="output directory")
    ap.add_argument("--list", action="store_true", help="list and exit")
    ap.add_argument("--smallest", action="store_true",
                    help="download only the smallest session (handy for a test)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)

    dev = Downloader(args.host, args.port)
    try:
        if dev.hello() is None:
            print(f"no response from {args.host}:{args.port}", file=sys.stderr)
            return 1

        payload = dev.session_list_raw()
        columns, rows = parse_sessions(payload)
        catalog = {r["name"]: r for r in rows_as_dicts(columns, rows)}
        print(f"{len(catalog)} sessions on device", file=sys.stderr)

        if args.list:
            for n, r in catalog.items():
                print(f"{n}  {int(r['size']):>10,}  {r['date']} {r['hour']}")
            return 0

        names = list(args.names)
        if args.smallest:
            names = [min(catalog, key=lambda n: int(catalog[n]["size"] or 0))]
        if not names:
            ap.error("give session names, or --smallest, or --list")

        failures = 0
        for name in names:
            meta = catalog.get(name)
            expected = int(meta["size"]) if meta and meta["size"] else None
            dest = os.path.join(args.out, name)
            print(f"\n  {name} -> {dest}"
                  + (f"  ({expected:,} bytes)" if expected else ""), file=sys.stderr)

            with open(dest, "wb") as fh:
                size, written = dev.fetch(
                    name, fh, progress=None if args.quiet else _progress)
            if not args.quiet:
                sys.stderr.write("\n")

            ok = True
            if size is not None and written != size:
                print(f"    ! short read: {written} of {size}", file=sys.stderr)
                ok = False
            if expected is not None and written != expected:
                print(f"    ! size mismatch: got {written}, catalog says "
                      f"{expected}", file=sys.stderr)
                ok = False
            if ok:
                print(f"    ok  {written:,} bytes", file=sys.stderr)
            else:
                failures += 1
        return 1 if failures else 0
    finally:
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
