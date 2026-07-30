# MyChron6 direct download — protocol reverse-engineering notes

Goal: pull session data off the MyChron6 without driving the Race Studio 3 GUI,
replacing `src/media_tools/ingest/rs3.py` with a direct client.

Status: **recon complete, no wire capture yet.** Everything below comes from
static analysis of `C:\AIM_SPORT\RaceStudio3\64\AiMRS3-64-ReleaseU.exe` and from
RS3's own logs in `C:\AIM_SPORT\RaceStudio3\logs\`. Nothing here has been
confirmed against live traffic.

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

There is no public SDK or protocol documentation. The only existence proof that
the protocol is reverse-engineerable is the LapSnap phone app, which syncs AiM
devices over WiFi but publishes nothing about how.

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
transport-independent**, which is the single most useful fact here: traffic
captured over *any* transport teaches the same protocol, and a WiFi client and a
USB client differ only in framing.

Discovery is a UDP subnet sweep plus multicast, with a keepalive that has at
least two versions:

- `CInterfacciaReteWin32::CreaAndBindSocketUDP`, `BindSocketUDP_multicast`
- `CThreadGatherDeviceSuReteMultiCast`, `CInterfacciaReteWin32::lookForDevices`
- `ValidaKeepAliveV2`, `ValidaKeepAliveV3`, `aliveDevice`
- `CInterfacciaReteWiFi::RispondeUnDevAiM` ("does an AiM device answer")

Logs confirm the sweep is brute force over the whole /24 — `253 probes in
~3.94 s`, repeating every ~7 s.

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

Transfer engine: `CTrasferitoreClassico::Scarica` and
`CTrasferitoreConCallback::Scarica` (the callback variant is what drives the
progress bar).

This is very good news for the effort estimate: the job is reversing a handful
of generic primitives — *identify*, *list directory*, *get file* — not an
opaque telemetry format. `leggiPropFilesRegistrati` + `scaricaFileRegistrato`
is the entire path we need.

Known command name from the logs: `IDENTITISSIMA` (device identification,
`CConnessione::InviaCmdLcyIdentitissima`). It is the natural first packet to
capture and the natural first packet to replay.

## What is still unknown

Static strings give function names, never wire format. Still needed:

1. TCP port the device listens on, and the UDP discovery port / multicast group.
2. Frame layout: magic, length, opcode, sequence, checksum.
3. Opcode values for identify / list / get-file.
4. Whether there is any authentication or obfuscation beyond the framing.

All four fall out of one captured download.

## Capture plan

`tools/research/capture_mychron.ps1` wraps `pktmon` (built into Windows 10 —
nothing to install, no download needed) and emits a `.pcapng`.

Note the *only* blocking dependency is physical: **no RS3 log on this machine
has ever shown a network device connection** (`CConnessioneTcp` /
`RispondeUnDevAiM` hits: 0 across all 11 logs). Every download so far was USB,
so there is no historical WiFi traffic to mine — one live WiFi download has to
be performed and captured.

Steps:

1. On the MyChron6: enable WiFi and note the `AiM-…` SSID.
2. Join that network from the PC (or put both on the same LAN).
3. Start the capture script.
4. In RS3, do one normal session download.
5. Stop the capture; analyse the pcapng.

Prefer a session with a **small** `.xrk` so the payload does not bury the
control messages.
