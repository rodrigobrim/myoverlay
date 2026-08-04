"""MyChron6 over USB.

The logger is a vendor-class device (USB Class FF) behind AiM's own kernel
driver - not HID - so it is driven through DeviceIoControl on the driver's
device interface. No admin rights, no WinUSB, no driver replacement.

Driver interface, recovered from AIM_USBdrv_11CC_0110_64a.sys:

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
                  +0x08 u32 length          (<= MAX_BULK)
                  +0x0c u16 timeout ms
                  +0x0e u8  endpoint        (0x81 IN, 0x02 OUT)
                  +0x10 u32 bytes transferred (out)
                  +0x14 u32 status flags      (out)

Two behaviours are easy to miss and both look like a wrong request format:

  * The descriptor + driver-info preamble is mandatory. Without it, on the
    same handle, every command comes back as an empty frame.
  * The device serves at most CHUNK_MAX bytes per payload and produces the
    next only after a zero-length control OUT with bRequest 2. That same
    call also terminates a finished transfer - skip it and the logger stays
    busy until it is power-cycled.
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
DEFAULT_TIMEOUT = 0x1388          # 5000 ms, matching AiM's own software
TEARDOWN_TIMEOUT = 300            # ms; close() must not hang on a dead device

OP_IDENTIFY = 0x00010010
OP_SESSION_LIST = 0x00020024
OP_GET_FILE = 0x00040002

SESSION_DIR = "1:/mem/"
MAX_BULK = 0x10000                # driver rejects longer single reads
CHUNK_MAX = 64512                 # most the device will serve at once

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


class Transport:
    """MyChron6 USB transport."""

    kind = "USB"

    def __init__(self, path: str = DEVICE_PATH, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self._seq = 0x30
        h = _k32.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
        if not h or h == _INVALID:
            err = ctypes.get_last_error()
            raise UsbError(f"cannot open the logger (winerr {err}); "
                           "is it plugged in?")
        self.h = h
        self._preamble()

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

    def _control(self, direction, data, length, breq=0x01, wval=0,
                 timeout=None):
        req = bytearray(24)
        req[0] = direction
        req[1] = breq
        struct.pack_into("<H", req, 2, wval)
        struct.pack_into("<H", req, 6, length)
        struct.pack_into("<Q", req, 8, ctypes.addressof(data))
        struct.pack_into("<I", req, 16,
                         self.timeout if timeout is None else timeout)
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

    def _request_next(self, timeout=None):
        """Zero-length control OUT (bRequest 2): 'send the next payload'.

        Also terminates a completed transfer, which is why it is sent after
        the final chunk too.
        """
        dummy = ctypes.create_string_buffer(1)
        self._control(DIR_OUT, dummy, 0, breq=0x02, wval=0, timeout=timeout)

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
        """Send a command; return (reply_bytes, length_field).

        Takes the FIRST matching reply. Reading past it consumes replies the
        device means for later commands and desyncs the link - the payload
        size comes from wait_payload() instead.
        """
        out = ctypes.create_string_buffer(self._body(opcode, arg), 64)
        if not self._control(DIR_OUT, out, 64):
            raise UsbError(f"sending opcode {opcode:#x} failed")

        reply = ctypes.create_string_buffer(64)
        for _ in range(tries):
            ctypes.memset(reply, 0, 64)
            self._control(DIR_IN, reply, 64)
            raw = bytes(reply.raw)
            if raw[:3] == b"cpa" and struct.unpack_from("<I", raw, 8)[0] == opcode:
                return raw, struct.unpack_from("<I", raw, 16)[0]
            time.sleep(0.05)
        raise UsbError(f"no reply to opcode {opcode:#x}")

    def wait_payload(self, tries: int = 200) -> int:
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

    # ---------------------------------------------------------------- public
    def session_csv(self) -> str:
        from ...catalog import strip_status_word
        self.command(OP_IDENTIFY)
        _reply, length = self.command(OP_SESSION_LIST)
        ready = self.wait_payload()
        return strip_status_word(self._bulk_in(ready or length))

    def fetch(self, name: str, fh, expected: int, progress=None) -> int:
        """Stream a session into `fh`; returns bytes written.

        `expected` comes from the listing: the command reply's own length
        field is often zero on the first reply.
        """
        path = name if (len(name) > 2 and name[0].isdigit()
                        and name[1] == ":") else SESSION_DIR + name
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
            self._request_next()
        return written

    def close(self):
        """Terminate the session, then release the handle.

        This is what AiM's own software does: its last call before CloseHandle is a
        single zero-length control OUT with bRequest 2, and nothing else - no
        draining, no polling. Every transfer already ends with the same call,
        so by the time we get here the logger is idle and this is one round
        trip. A short timeout only guards against a device that has been
        unplugged mid-run; it is not a wait we expect to spend.
        """
        if not getattr(self, "h", None):
            return
        try:
            self._request_next(timeout=TEARDOWN_TIMEOUT)
        except Exception:
            pass                      # never let teardown mask a real error
        finally:
            _k32.CloseHandle(w.HANDLE(self.h))
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
