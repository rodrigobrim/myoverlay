# AiM MyChron6 — confirmed wire findings (live device, 2026-07-31)

Device: MyChron6 Brim, serial 35021763, AP `AiM-MYC6-021763-MyChron6 Brim` (open, no password).
Device IP `10.0.0.1` (it is the DHCP server + gateway); PC gets `10.0.0.2/24`.

## Confirmed working: UDP discovery / keepalive

* Port: **UDP 36002** — confirmed in code via `htons(0x8ca2)` at `0x141a7bc53`.
* Probe: the literal ASCII bytes **`aim-ka`** (6 bytes), sent with `sendto`,
  16-byte AF_INET sockaddr. Built at `0x141a52c07..0x141a52c2f` in
  `CInterfacciaReteWin32::aliveDevice` (`0x141a52a10`).
* Works unicast to `10.0.0.1:36002` and to broadcast `10.0.0.255:36002`.
* Device replies with a **236-byte descriptor**. Reply is reliable + repeatable.

A second probe exists, `aim-kb` + `<ver>` + `<b7>` + `0x01` (9 bytes), built at
`0x141a7bc6d` inside the discovery engine `0x141a7b260`. Byte[6] is the protocol
version (`ValidaKeepAliveV3` requires >= 3). The live device did NOT answer
`aim-kb` for ver in {2,3,4} and b7 in {0,1} — byte 7 comes from a call at
`0x141a7bc8a` (`[rax+3]`) and is not yet identified.

### Decoded reply (236 bytes)

```
off 0x00  ec 00 00 00   u32 LE  = 236   total length (includes this field)
off 0x04  02 00 00 00   u32 LE  = 2     protocol version
off 0x08  0a 00 00 01           = 10.0.0.1   device IP (network order)
off 0x0c  00 00
off 0x0e  06 00                         device family? (MyChron"6")
off 0x14  "MyChron6 Brim"               device name, NUL-padded
off 0x55  "idn" 01 38 00 35 02 b5 01 00 00
off ~0x5e c3 63 16 02   u32 LE  = 0x021663C3 = 35021763   SERIAL (matches doc)
off ~0xab "Sissilandia"                 venue / track name
```

## Still unknown: TCP command framing

* Only open TCP port: **2000**. Device accepts the connection, never resets,
  never replies to malformed input.
* Tested 18 framings after a *successful* `aim-ka` handshake, using opcodes
  `0x00010002` (IdentificaRete), `0x00010010` (LcyIdentitissima),
  `0x00040005` (leggiPropFiles): `<len><cmd>` with len incl/excl, `<len><cmd><plen>`,
  `<len><ver><cmd>`, etc. **All silent.**
* Conclusion: the handshake works but does NOT gate TCP. The TCP framing is
  genuinely different and must come from
  `CConnessioneTcp::InviaCmdStdGenerico` @ `0x141ad0340` (~2181 instructions,
  writes via virtual dispatch, almost no logging).

## Serialization trace (attempted) — why the wire format is not in the call path

* Socket abstraction identified by reverse RTTI: **`SocketRemote`**
  (`.?AVSocketRemote@@`), vtable `0x142d69e08` — `send` = slot[14] (`+0x70`,
  `0x1422eaf70`), `recv` = slot[13] (`+0x68`, `0x1422eabd0`). Sibling
  `SocketBase` vtable `0x142d6d290`. Both are generic library classes.
* **There is no `send()` call anywhere in AiM's own network code** — only
  `sendto` (UDP). All 5 `send()` sites live in statically-linked library code.
* `CConnessioneTcp::InviaCmdStdGenerico` (`0x141ad0340`) contains **no socket
  dispatch at all**. Forward reachability over 187 functions / 4 hops finds no
  path to a socket write.
* Its most-called helper `sub_141a4d630` (29 calls) is a **mutex-protected trace
  ring buffer**: spinlock `sub_14211b18c` + `Sleep(1)`, a global monotonic
  counter, 80-byte entries into an array at `this+8` spanning ~`0x13888` bytes
  (~1000 entries). The other frequent callees
  (`sub_141a75fc0` / `sub_141a77590` / `sub_1413a3440` + `call [rax+0x30]`)
  are the logging idiom.

=> The command path is **asynchronous**: `InviaCmdStdGenerico` records the
request and hands it to a task/worker thread that owns the socket. Consistent
with the `CAiMTask*` template family, `CTaskUSB2GSingleDevLiveViewKeepAlive`,
and the `aim-lib-device-comm` library name. AiM creates threads via CRT
`_beginthreadex` (static, not an import), so the worker proc is not reachable
by scanning `CreateThread` call sites.

Caution when scanning: connection-vtable `InviaCmdStdGenerico` is ALSO at
`+0x70`, so `call [reg+0x70]` sites collide with `SocketRemote::send`.

### Empirically refuted
* TCP does not require the UDP handshake (silent after a successful `aim-ka`).
* TCP is not blocking on a fixed-size header: frames padded 8→256 bytes, with
  the opcode at offsets 0/4/8/12/16, plus `aim-ka`/`aim-kb` over TCP — all
  silent, no reset, no close. 39 distinct framings tested in total.

## Command layer (from static analysis)

`InviaCmdStdGenerico` signature, from `IdentificaRete` @ `0x141acd150`:

```
(this, ctx, u32 cmd /*r8d*/, u32 inLen /*r9d*/, void* inData,
 Buffer* out, bool, bool, bool, bool, u32 timeoutMs)
```

Opcode layout: `(group << 16) | command`.
Listing path: `leggiPropFilesRegistrati` (`0x141aadd90`) -> `leggiPropFiles`
(`0x141aa92a0`) -> three `InviaCmdStdGenerico` calls
(`0x00040005`, `0x00040019`, `0x0004001a`).

Vtable (RTTI-resolved): slot `+0x48` `InviaCmdLcyIdentitissima` is shared by ALL
transports; only `+0x70` `InviaCmdStdGenerico` is transport-specific
(TCP `0x141ad0340`, USB `0x141a5d450`). Transport-independence confirmed.

See `aim_command_table.txt` for all 109 opcodes.

## DESTRUCTIVE opcodes — never send by accident

`0x00040006` DbgDelFile, `0x00040007` removeDir/DbgDelDir,
`0x00040008` delDirContent, `0x00040009` DbgFormatMedia,
`0x0004000d` DbgFormatDati, `0x00051000` eraseFirmware,
`0x000c0005` DbgResetWiFi.
