"""Trace RS3's DeviceIoControl calls to the AiM USB driver.

Attaches to Race Studio 3 and logs every IOCTL: code, input buffer, output
buffer. This is the layer USBPcap cannot see - it sits above the driver.

    python trace_ioctl.py            # attach to a running RS3
    python trace_ioctl.py --spawn    # launch RS3 and attach immediately
"""
import sys, time, frida

RS3 = r"C:\AIM_SPORT\RaceStudio3\64\AiMRS3-64-ReleaseU.exe"
OUTFILE = "ioctl_trace.txt"

JS = r"""
const AIM_MASK = 0xffff0000;
const AIM_BASE = 0x00220000;

function hexdump_bytes(ptr, len) {
    if (ptr.isNull() || len === 0) return "";
    const n = Math.min(len, 96);
    try { return Array.from(new Uint8Array(ptr.readByteArray(n)))
        .map(b => b.toString(16).padStart(2, '0')).join(' '); }
    catch (e) { return "<unreadable>"; }
}

// RS3 may reach DeviceIoControl through kernel32, KernelBase or ntdll, so
// hook every one we can resolve and dedupe by address.
const seen = {};
const targets = [];
function resolve(mod, sym) {
    // Frida 17 removed Module.getExportByName; try the new API first.
    try { return Process.getModuleByName(mod).getExportByName(sym); } catch (e) {}
    try { return Module.getGlobalExportByName(sym); } catch (e) {}
    try { return Module.getExportByName(mod, sym); } catch (e) {}
    return null;
}
for (const [mod, sym] of [['kernel32.dll', 'DeviceIoControl'],
                          ['KernelBase.dll', 'DeviceIoControl']]) {
    const a = resolve(mod, sym);
    if (a && !seen[a.toString()]) { seen[a.toString()] = 1; targets.push([mod, sym, a]); }
    else if (!a) console.log('[frida] could not resolve ' + mod + '!' + sym);
}
targets.forEach(t => console.log('[frida] hooking ' + t[0] + '!' + t[1] + ' @ ' + t[2]));

targets.filter(t => t[1] === 'DeviceIoControl').forEach(t => Interceptor.attach(t[2], {
    onEnter(args) {
        this.code = args[1].toInt32() >>> 0;
        this.inBuf = args[2];
        this.inLen = args[3].toInt32();
        this.outBuf = args[4];
        this.outLen = args[5].toInt32();
        this.ret = args[6];
        this.interesting = (this.code & AIM_MASK) === AIM_BASE;
        if (this.interesting) {
            this.inHex = hexdump_bytes(this.inBuf, this.inLen);
            // For the control (0x22000c) and bulk (0x220010) requests the real
            // payload is behind a pointer inside the request struct, so follow it.
            this.dataPtr = NULL; this.dataLen = 0;
            try {
                if (this.code === 0x0022000c && this.inLen >= 16) {
                    this.dataPtr = this.inBuf.add(8).readPointer();
                    this.dataLen = this.inBuf.add(6).readU16();
                    this.dir = this.inBuf.readU8();
                } else if (this.code === 0x00220010 && this.inLen >= 16) {
                    this.dataPtr = this.inBuf.readPointer();
                    this.dataLen = this.inBuf.add(8).readU32();
                    this.dir = this.inBuf.add(14).readU8();
                }
            } catch (e) {}
            this.dataBefore = hexdump_bytes(this.dataPtr, this.dataLen);
        }
    },
    onLeave(retval) {
        if (!this.interesting) return;
        let outHex = "";
        let returned = 0;
        try { if (!this.ret.isNull()) returned = this.ret.readU32(); } catch (e) {}
        outHex = hexdump_bytes(this.outBuf, Math.min(this.outLen, returned || this.outLen));
        send({
            code: this.code, ok: retval.toInt32(),
            inLen: this.inLen, outLen: this.outLen, returned: returned,
            inHex: this.inHex, outHex: outHex,
            dir: this.dir, dataLen: this.dataLen,
            dataBefore: this.dataBefore,
            dataAfter: hexdump_bytes(this.dataPtr, this.dataLen)
        });
    }
}));
console.log("[frida] hooks installed");
"""

fh = open(OUTFILE, "w", encoding="utf-8")
count = 0


def on_message(msg, data):
    global count
    if msg.get("type") == "log":
        line = "[script] " + msg.get("payload", "")
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()
        return
    if msg.get("type") != "send":
        print("  !", msg, flush=True)
        return
    p = msg["payload"]
    count += 1
    line = (f"\n#{count}  IOCTL {p['code']:#010x}  ok={p['ok']} "
            f"inLen={p['inLen']} outLen={p['outLen']} returned={p['returned']}\n"
            f"    in : {p['inHex']}\n"
            f"    out: {p['outHex']}\n"
            f"    data.in : {p.get('dataBefore','')}\n"
            f"    data.out: {p.get('dataAfter','')}")
    print(line, flush=True)
    fh.write(line + "\n")
    fh.flush()


if "--spawn" in sys.argv:
    pid = frida.spawn([RS3])
    session = frida.attach(pid)
    print(f"spawned RS3 pid={pid}")
else:
    session = frida.attach("AiMRS3-64-ReleaseU.exe")
    pid = None
    print("attached to running RS3")

script = session.create_script(JS)
script.on("message", on_message)
script.load()
if pid:
    frida.resume(pid)

print(f"tracing... writing to {OUTFILE}   (Ctrl-C or kill to stop)")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    pass
fh.close()

