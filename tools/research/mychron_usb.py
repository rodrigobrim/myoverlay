"""USB transport for the AiM MyChron6 - same command layer as WiFi.

The logger is a vendor-class USB device (Class FF) behind AiM's own kernel
driver, so it is reached through DeviceIoControl rather than HID or WinUSB.
No admin rights and no driver replacement are needed.

Driver interface (recovered from AIM_USBdrv_11CC_0110_64a.sys):

    0x220000  get USB config descriptor      (256-byte buffer)
    0x220014  get driver info                (48-byte, 'dpr' tag)
    0x22000c  vendor control transfer, 24-byte request:
                  +0x00 u8  direction   0x40 = OUT, 0xc0 = IN
                  +0x01 u8  bRequest
                  +0x02 u16 wValue
                  +0x04 u16 wIndex
                  +0x06 u16 wLength
                  +0x08 u64 data buffer
                  +0x10 u32 timeout ms
                  +0x14 u32 status flags   (bit0 ok)
                  +0x16 u16 bytes transferred
    0x220010  bulk transfer, 24-byte request:
                  +0x00 u64 data buffer
                  +0x08 u32 length          (<= 0x10000)
                  +0x0c u16 timeout ms
                  +0x0e u8  endpoint        (0x81 IN, 0x02 OUT)
                  +0x10 u32 bytes transferred (out)
                  +0x14 u32 status flags      (out)

The descriptor + driver-info preamble matters: without it the device answers
commands with empty frames.

Command flow, e.g. the session list:
    control OUT   'cpa' header + 64-byte body with the opcode at +8
    control IN    reply; payload length lands at +16
    control IN    bRequest 2, 8 bytes: [state, length]; state 0 = ready
    bulk IN       read `length` bytes from endpoint 0x81
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import struct
import time

DEVICE_PATH = (r"\\?\USB#VID_11CC&PID_0110#1.0"
               r"#{f8e1b2bb-efb2-4cb3-b148-71108974e8ff}")

IOCTL_DESCRIPTOR = 0x220000
IOCTL_CONTROL = 0x22000C
IOCTL_BULK = 0x220010
IOCTL_DRIVER_INFO = 0x220014

DIR_OUT, DIR_IN = 0x40, 0xC0
EP_BULK_IN = 0x81
DEFAULT_TIMEOUT = 0x1388          # 5000 ms, same as RS3

OP_IDENTIFY = 0x00010010
OP_SESSION_LIST = 0x00020024
OP_GET_FILE = 0x00040002

SESSION_DIR = "1:/mem/"
MAX_BULK = 0x10000                # driver rejects longer single reads

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = w.HANDLE
_k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                             w.DWORD, w.DWORD, w.HANDLE]
_k32.DeviceIoControl.restype = w.BOOL
_k32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD,
                                 ctypes.c_void_p, w.DWORD,
                                 ctypes.POINTER(w.DWORD), ctypes.c_void_p]
_k32.CloseHandle.restype = w.BOOL
_k32.CloseHandle.argtypes = [w.HANDLE]

_INVALID = ctypes.c_void_p(-1).value


class UsbError(RuntimeError):
    pass


class MyChronUSB:
    def __init__(self, path: str = DEVICE_PATH, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._seq = 0x30
        h = _k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
        if not h or h == _INVALID:
            err = ctypes.get_last_error()
            raise UsbError(f"cannot open {path} (winerr {err}); "
                           "is the logger plugged in?")
        self.h = h
        self._preamble()
        self._drain_pending()

    # -------------------------------------------------------------- plumbing
    def _ioctl(self, code, buf, size):
        ret = w.DWORD(0)
        ok = _k32.DeviceIoControl(w.HANDLE(self.h), code, buf, size, buf, size,
                                  ctypes.byref(ret), None)
        return bool(ok), ret.value

    def _preamble(self):
        """The device stays mute unless this runs first on the same handle."""
        b = ctypes.create_string_buffer(256)
        self._ioctl(IOCTL_DESCRIPTOR, b, 256)
        info = bytearray(48)
        info[0:6] = b"dpr\x01\x28\x00"
        ib = ctypes.create_string_buffer(bytes(info), 48)
        self._ioctl(IOCTL_DRIVER_INFO, ib, 48)

    def _drain_pending(self, rounds: int = 8):
        """Discard a payload left queued by an interrupted transfer.

        The device serves one payload at a time; if a previous run asked for
        a file and never read it, every later command answers with nothing
        until that data is consumed.
        """
        dropped = 0
        sb = ctypes.create_string_buffer(8)
        for _ in range(rounds):
            ctypes.memset(sb, 0, 8)
            if not self._control(DIR_IN, sb, 8, breq=0x02, wval=0):
                return dropped
            state, length = struct.unpack("<II", sb.raw)
            if state != 0 or not length:
                return dropped
            self._bulk_in(min(length, MAX_BULK))
            dropped += 1
        return dropped

    def _control(self, direction, data, length, breq=0x01, wval=0):
        req = bytearray(24)
        req[0] = direction
        req[1] = breq
        struct.pack_into("<H", req, 2, wval)
        struct.pack_into("<H", req, 6, length)
        struct.pack_into("<Q", req, 8, ctypes.addressof(data))
        struct.pack_into("<I", req, 16, self.timeout)
        rb = ctypes.create_string_buffer(bytes(req), 24)
        ok, _ = self._ioctl(IOCTL_CONTROL, rb, 24)
        flags = struct.unpack_from("<I", rb.raw, 20)[0]
        return ok and bool(flags & 1)

    def _bulk_in(self, length):
        buf = ctypes.create_string_buffer(length)
        req = bytearray(24)
        struct.pack_into("<Q", req, 0, ctypes.addressof(buf))
        struct.pack_into("<I", req, 8, length)
        struct.pack_into("<H", req, 12, self.timeout)
        req[14] = EP_BULK_IN
        rb = ctypes.create_string_buffer(bytes(req), 24)
        ok, _ = self._ioctl(IOCTL_BULK, rb, 24)
        got = struct.unpack_from("<I", rb.raw, 16)[0]
        if not ok:
            raise UsbError("bulk read failed")
        return bytes(buf.raw[:got])

    # ------------------------------------------------------------- commands
    def _body(self, opcode: int, arg: bytes = b"") -> bytes:
        self._seq = (self._seq + 1) & 0xFFFF
        b = bytearray(64)
        b[0:4] = b"cpa\x01"
        struct.pack_into("<H", b, 4, 0x0038)
        struct.pack_into("<H", b, 6, self._seq)
        struct.pack_into("<I", b, 8, opcode)
        struct.pack_into("<I", b, 24, 0x00000A01)
        if arg:
            b[32:32 + len(arg) + 1] = arg + b"\x00"
        return bytes(b)

    def command(self, opcode: int, arg: bytes = b"", tries: int = 20):
        """Send a command; return (reply_bytes, payload_length)."""
        out = ctypes.create_string_buffer(self._body(opcode, arg), 64)
        if not self._control(DIR_OUT, out, 64):
            raise UsbError(f"sending opcode {opcode:#x} failed")

        # Take the FIRST matching reply and stop. Polling past it consumes
        # replies the device intends for later commands and desyncs the link;
        # the payload length is read from wait_payload() instead of here.
        reply = ctypes.create_string_buffer(64)
        for _ in range(tries):
            ctypes.memset(reply, 0, 64)
            self._control(DIR_IN, reply, 64)
            raw = bytes(reply.raw)
            if raw[:3] == b"cpa" and struct.unpack_from("<I", raw, 8)[0] == opcode:
                return raw, struct.unpack_from("<I", raw, 16)[0]
            time.sleep(0.05)
        raise UsbError(f"no reply to opcode {opcode:#x}")

    def wait_payload(self, tries: int = 200):
        """Poll [state, length]; state 0 means the payload is ready."""
        sb = ctypes.create_string_buffer(8)
        for _ in range(tries):
            ctypes.memset(sb, 0, 8)
            self._control(DIR_IN, sb, 8, breq=0x02, wval=0)
            state, length = struct.unpack("<II", sb.raw)
            if state == 0 and length:
                return length
            time.sleep(0.02)
        raise UsbError("payload never became ready")

    def session_list_raw(self) -> bytes:
        """The device's CSV listing, verbatim."""
        self.command(OP_IDENTIFY)
        _reply, length = self.command(OP_SESSION_LIST)
        ready = self.wait_payload()
        return self._bulk_in(ready or length)

    def get_file(self, remote: str, fh, expected: int, progress=None) -> int:
        """Stream a device file into `fh`; returns bytes written.

        Sessions live at 1:/mem/<name>. `expected` comes from the listing -
        the command reply's own length field is often zero, and the driver
        caps a single bulk read at MAX_BULK, so larger files arrive as
        several reads, each preceded by its own readiness poll.
        """
        path = remote if (len(remote) > 2 and remote[0].isdigit()
                          and remote[1] == ":") else SESSION_DIR + remote
        self.command(OP_GET_FILE, path.encode("latin-1"))
        written = 0
        while written < expected:
            ready = self.wait_payload()
            want = min(ready or (expected - written), MAX_BULK, expected - written)
            chunk = self._bulk_in(want)
            if not chunk:
                raise UsbError(f"{path}: empty read at {written}/{expected}")
            fh.write(chunk)
            written += len(chunk)
            if progress:
                progress(written, expected)
            if written < expected:
                self._request_next()
        return written

    def _request_next(self):
        """Zero-length control OUT (bRequest 2) asking for the next chunk.

        The device serves at most CHUNK_MAX bytes per payload and will not
        produce the next one until this is sent - the USB counterpart of the
        byte cursor used over TCP.
        """
        dummy = ctypes.create_string_buffer(1)
        self._control(DIR_OUT, dummy, 0, breq=0x02, wval=0)

    def close(self):
        if getattr(self, "h", None):
            _k32.CloseHandle(w.HANDLE(self.h))
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def main(argv=None):
    import argparse, sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mychron_list import parse_sessions, render_table, rows_as_dicts
    import json

    ap = argparse.ArgumentParser(description="List MyChron sessions over USB")
    ap.add_argument("--format", choices=("table", "csv", "json"), default="table")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    with MyChronUSB() as dev:
        payload = dev.session_list_raw()

    # over USB the CSV arrives without the 4-byte status word the TCP path has
    text = payload.decode("latin-1")
    if not text.startswith("name,"):
        text = payload[4:].decode("latin-1")
    columns, rows = parse_sessions(b"\x00\x00\x00\x00" + text.encode("latin-1"))

    if args.format == "csv":
        rendered = text
    elif args.format == "json":
        rendered = json.dumps(rows_as_dicts(columns, rows), indent=2)
    else:
        rendered = render_table(columns, rows)

    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(rendered)
        print(f"{len(rows)} sessions, {len(columns)} columns -> {args.out}")
    else:
        print(rendered)
        print(f"\n{len(rows)} sessions, {len(columns)} columns", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
