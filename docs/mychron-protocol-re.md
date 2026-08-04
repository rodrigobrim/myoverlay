# MyChron6 direct download — protocol reverse-engineering notes

Goal: pull session data off the MyChron6 without driving the Race Studio 3 GUI.

Status: **done for the MyChron6, over both transports — and shipped.** The
client lives in `src/media_tools/aim/` and backs `mt telemetry list` /
`mt telemetry get` (via `src/media_tools/ingest/aim.py`). It lists sessions
and downloads them with no AiM software involved, over USB or WiFi, without
admin rights. The RS3 GUI automation it replaced has been removed from the
repo.

The protocol was recovered from three sources: static analysis of
`C:\AIM_SPORT\RaceStudio3\64\AiMRS3-64-ReleaseU.exe`, a Wireshark/Npcap
capture of one RS3 WiFi session, and a Frida trace of RS3's `DeviceIoControl`
calls for the USB side. Everything below has been confirmed against the live
device unless it says otherwise.

The full wire format is in [mychron-wifi-protocol.md](mychron-wifi-protocol.md);
the opcode table is [mychron-command-opcodes.txt](mychron-command-opcodes.txt).

## Why the device is not a USB drive

The MyChron6 enumerates under AiM's own kernel driver, not mass-storage and not
a serial port:

```
USB\VID_11CC&PID_0110   device code 437   serial 35021763   "MyChron6 Brim"
```

`VID_11CC` is AiM. There is no filesystem to mount and no `.xrk` on the device —
RS3 pulls raw session data over a custom protocol and *writes* the `.xrk` on the
PC side. That is why `libxrk`, `xdrk` and every other public tool only read
`.xrk` files that RS3 already produced; none of them talk to hardware.

Confirmed: the driver is `AIM_USBdrv_11CC_0110_64a.sys` (service
`AIM_USBdriver_0110`, provider `AIM_srl`) and the device is USB **Class FF**,
vendor-specific. Device Manager files it under the HID *setup class*, which is
only a grouping — it speaks no HID protocol, and `hidapi` cannot see it. Reach
it through `DeviceIoControl` on the driver's own device interface instead.

There is no public SDK or protocol documentation, and no public reverse
engineering of it that a web search can find.

## Architecture

Transports are siblings over one shared command layer. From the binary's RTTI
and log-format strings:

| Class | Role |
| --- | --- |
| `CConnessione` | base command layer — `InviaCmdLcyIdentitissima`, `impostaDataOra`, `leggiDataOra`, `leggiParamCFF` |
| `CConnessioneUSB` / `CConnessioneLIBUSB` | USB transport |
| `CConnessioneTcp` | **network transport (WiFi/Ethernet)** |
| `CConnessioneIPonUSB` | IP protocol tunnelled over the USB cable |

All four implement the same primitives — `InviaCmdStdGenerico` (send generic
standard command), `IdentificaRete`, `LeggiInfoDevice`. **The command layer is
transport-independent.**

Confirmed from the RTTI-resolved vtables: `InviaCmdLcyIdentitissima` is one
shared implementation at slot `+0x48` across every transport, and only
`InviaCmdStdGenerico` at `+0x70` differs (TCP `0x141ad0340`, USB
`0x141a5d450`). In practice USB and WiFi carry byte-identical 64-byte command
bodies with the same opcodes, and a file fetched over either hashes the same.

Discovery is UDP port **36002**. The probe is the literal ASCII string
`aim-ka`; the device replies with a 236-byte descriptor carrying its name, IP
and serial. A second probe form, `aim-kb` + version + byte + `0x01`, exists in
the binary but the device did not answer it.

## The device is a remote filesystem

`CStrumentoMXL2` is the instrument class for modern devices with a filesystem
(the MyChron6 is a "2G dev" in RS3's logs and shares this download path). Its
method list reads like a file-server API rather than a bespoke telemetry stream:

- **list**: `leggiPropFiles`, `leggiPropFilesRegistrati` (recorded sessions),
  `leggiSummaryFilesRegistrati`, `leggiPropFilesAbsTree`, `getPath`
- **download**: `scaricaFileRegistrato` (download a recorded session),
  `scaricaFileSingolo`, `scaricaFileMultipli`, `scaricaFilesAndCartelle`
- **upload / mutate**: `caricaFile`, `delFile`, `removeDir`, `scriviCfg*`
- **status**: `isReadyForComm`, `isMediaInserita`, `getMediaStatus`

Sessions live at `1:/mem/<name>`; volume `0:/` holds configuration, firmware
and track data. Two extensions appear — `.xrz` and `.hrz` — so do not filter on
one of them.

In practice only two commands are needed:
`leggiSummaryFilesRegistrati` (`0x00020024`) returns the listing as CSV with a
27-column header, and `getFile` (`0x00040002`) with a path streams the file.
The CSV carries lap counts, best lap in milliseconds, track name and
coordinates, so listing needs no `.xrk` parsing at all.

## Network details

The logger runs its own access point and DHCP server. It is **`10.0.0.1`** and
hands the PC `10.0.0.2/24` — not the `192.168.4.1` this document previously
guessed. The only open TCP port is **2000**. The device emits no ICMP at all,
so UDP port scanning cannot distinguish open from closed on it.

On this unit the access point is **open**, with no password. Note the profile
the AiM software leaves in Windows specifies WPA2, and that mismatch makes
connection attempts fail silently; the client writes its own throwaway open
profile rather than reusing it.

## What was still unknown, and how it was answered

1. **Ports** — TCP 2000, UDP 36002. From a port scan and `htons(0x8ca2)` in
   the binary.
2. **Frame layout** — from the WiFi capture. Frames are ASCII-tagged, which is
   why every plain length-prefixed guess was ignored: with no `<h` marker the
   device never sees a frame begin, accepts the connection and waits forever
   instead of resetting.
3. **Opcodes** — 109 of them, recovered by scanning all 634 command sites for
   the immediate loaded into `r8d`. Layout is `(group << 16) | command`.
4. **Authentication** — there is none, and no obfuscation beyond the framing.

## Capture tooling

`tools/research/capture_mychron.ps1` wraps `pktmon` and needs admin. In
practice Wireshark's bundled **Npcap** was easier: installed with the
admin-only restriction unchecked, `dumpcap` captures without elevation.

For USB, **USBPcap** ships with Wireshark and captures URBs — but it sits
*below* AiM's driver, so it shows the wire protocol and not the
`DeviceIoControl` interface needed to drive it. That gap was closed with a
Frida script hooking `DeviceIoControl` inside RS3
(`tools/research/trace_ioctl.py`), which is what revealed the mandatory
descriptor preamble and the chunk handshake.

## Beyond the MyChron6

None of the command layer here is known to generalise. RS3 splits devices
across `CStrumentoMXL2` (2G, has a filesystem — what we implement) and the
`CStrumentoSpansionNoFileSystem` family, which is older hardware with no
filesystem and a different download path entirely. An AiM Solo or an older
MyChron may well fall in the second group.

What should carry over is everything below the instrument class: the framing,
the `aim-ka` discovery, the driver IOCTLs. What will not: the `1:/mem/` paths,
the CSV listing, the hardcoded USB PID `0110`, and the assumption of an open
access point.

The clean way to extend this is to identify the device first —
`CStrumentoDaIdentificare` is literally RS3's "device to be identified" class,
and the `aim-ka` descriptor already carries a family field — then dispatch to a
per-model package under `src/media_tools/aim/`.
