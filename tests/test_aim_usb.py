"""USB transport dialogue (aim/mychron/v6/usb.py) against a scripted device.

The Win32 plumbing (_control/_bulk_in/_request_next) is replaced with a
script of canned states, replies and payloads; everything above it - the
command loop, payload waits and the firmware-667 drain - runs for real.
"""

import struct

import media_tools.aim.mychron.v6.usb as usb


def frame(opcode: int, length: int = 0) -> bytes:
    """A 64-byte command reply as the device sends it."""
    b = bytearray(64)
    b[0:4] = b"cpa\x01"
    struct.pack_into("<I", b, 8, opcode)
    struct.pack_into("<I", b, 16, length)
    return bytes(b)


class ScriptedTransport(usb.Transport):
    """usb.Transport with the ioctl layer replaced by scripts.

    states: (state, length) pairs served per payload-state poll; the last
    entry repeats forever. replies: 64-byte frames served per command-reply
    read (zeros when exhausted). bulks: payloads served per bulk read.
    """

    def __init__(self, states, replies=(), bulks=()):
        self.timeout = 0
        self._seq = 0x30
        self.h = None
        self.states = list(states)
        self.replies = list(replies)
        self.bulks = list(bulks)
        self.requested_next = 0

    def _control(self, direction, data, length, breq=0x01, wval=0,
                 timeout=None):
        if direction == usb.DIR_IN and breq == 0x02 and length == 8:
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            struct.pack_into("<II", data, 0, *state)
        elif direction == usb.DIR_IN and length == 64:
            reply = self.replies.pop(0) if self.replies else b"\x00" * 64
            data[0:64] = reply
        return True

    def _bulk_in(self, length):
        return self.bulks.pop(0) if self.bulks else b""

    def _request_next(self, timeout=None):
        self.requested_next += 1


def test_drain_reads_staged_bundle_and_terminates():
    # Parked bundles keep announcing their length after the data is read
    # out (observed on firmware 667): the empty follow-up read must end the
    # drain, not loop on the announcement.
    t = ScriptedTransport(states=[(0, 0), (0x14, 4327)],
                          bulks=[b"x" * 4327, b""])
    assert t._drain_payloads(wait_s=0.5) == 4327
    assert t.requested_next == 2  # one per read, including the spent one


def test_drain_spends_the_wait_quietly_on_old_firmware():
    t = ScriptedTransport(states=[(0, 0)])
    assert t._drain_payloads(wait_s=0.05) == 0
    assert t.requested_next == 0


def test_session_csv_drains_identify_bundle_before_listing():
    csv = b"name,size\r\na_0271.xrz,123\r\n"
    t = ScriptedTransport(
        # identify's bundle announces as 0x14 until spent, then the
        # session-list payload stages as a fresh state-0 entry.
        states=[(0x14, 20), (0x14, 20), (0, len(csv))],
        replies=[frame(usb.OP_IDENTIFY, 20), frame(usb.OP_SESSION_LIST, 0)],
        bulks=[b"B" * 20, b"", csv],
    )
    assert t.session_csv() == csv.decode()
    assert not t.bulks  # the catalog bulk was consumed by the listing
    assert t.requested_next == 2  # both drain reads; the catalog read sends none
