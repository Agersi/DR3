"""Minimal Windows minidump parser for the DR2 car-select crash dump.

Extracts: exception record (code/address), faulting thread stack, module
list — maps stack addresses to module + RVA so the crash site can be
located inside dirtrally2.exe without symbols.

Run: python tools\\parse_crashdump.py [path-to-dmp]
"""
import struct
import sys

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DMP = (sys.argv[1] if len(sys.argv) > 1 else
       r"D:\CODE\DR2\reference\crash_dump\crashdump.dmp")

data = open(DMP, "rb").read()
sig, ver, nstreams = struct.unpack_from("<4sII", data, 0)
assert sig == b"MDMP", sig
print(f"minidump v{ver}, {nstreams} streams, {len(data)} bytes")

# MINIDUMP_HEADER is 32 bytes; stream directory starts at offset 32.
entries = []
for i in range(nstreams):
    st, size, rva = struct.unpack_from("<III", data, 32 + i * 12)
    entries.append((st, size, rva))

streams = {}
for st, size, rva in entries:
    streams.setdefault(st, []).append((size, rva))

EXC_STREAM, THREAD_LIST, MODULE_LIST = 1, 2, 15

modules = []
if MODULE_LIST in streams:
    size, rva = streams[MODULE_LIST][0]
    nmod = struct.unpack_from("<I", data, rva)[0]
    # MINIDUMP_MODULE: BaseOfImage(8) SizeOfImage(4) TimeDateStamp(4)
    # ModuleNameRva(4) VersionInfoSize(1) Reserved1(1) VS_FIXEDFILEINFO(52)
    # -> padded to 76
    for i in range(nmod):
        e = rva + 4 + i * 76
        base, sz, _ts, name_rva = struct.unpack_from("<QII I", data, e)
        nm = b""
        p = name_rva
        while p < len(data) and data[p:p + 1] != b"\x00" and len(nm) < 256:
            nm += data[p:p + 1]
            p += 1
        modules.append((base, sz, nm.decode("utf-16-le", "replace")))
modules.sort(key=lambda m: m[0])


def mod_of(addr):
    for base, sz, nm in modules:
        if base <= addr < base + sz:
            return nm, addr - base
    return None, None


print(f"\nmodules ({len(modules)}):")
for base, sz, nm in modules:
    print(f"  0x{base:012X} len=0x{sz:X} {nm}")

fault_tid = None
if EXC_STREAM in streams:
    size, rva = streams[EXC_STREAM][0]
    code, flags = struct.unpack_from("<II", data, rva)
    rec_ip = struct.unpack_from("<Q", data, rva + 16)[0]
    nparams = struct.unpack_from("<I", data, rva + 24)[0]
    info0 = struct.unpack_from("<Q", data, rva + 32)[0]
    fault_tid = struct.unpack_from("<I", data, rva + 48)[0]
    print(f"\nEXCEPTION: code=0x{code:08X} flags=0x{flags:X} "
          f"address=0x{rec_ip:012X} thread={fault_tid}")
    if info0:
        print(f"  ExceptionInformation[0] = 0x{info0:X} "
              f"(access {('WRITE' if info0 & 1 else 'READ')})")
    m, off = mod_of(rec_ip)
    print(f"  -> {m} + 0x{off:X}" if m else "  -> not in any module")

if THREAD_LIST in streams:
    size, rva = streams[THREAD_LIST][0]
    nthr = struct.unpack_from("<I", data, rva)[0]
    print(f"\nthreads: {nthr}")
    # x64 MINIDUMP_THREAD: ThreadId(4) ClientOffsetOfThreadContext(4)
    # CONTEXT(720) Stack{Start(8),Size(8)} Memory{Start(8),Size(8)} Resv(8)
    STRIDE = 8 + 720 + 16 + 16 + 8
    for i in range(nthr):
        e = rva + 4 + i * STRIDE
        tid = struct.unpack_from("<I", data, e)[0]
        if fault_tid is not None and tid != fault_tid:
            continue
        stack_start, stack_size = struct.unpack_from(
            "<QQ", data, e + 8 + 720)
        print(f"--- thread {tid} (stack at 0x{stack_start:X}, "
              f"{stack_size} bytes) ---")
        nwords = min(stack_size // 8, 160)
        for w in range(nwords):
            a = struct.unpack_from("<Q", data, stack_start + w * 8)[0]
            if not a:
                continue
            m, off = mod_of(a)
            if m:
                print(f"  [0x{w:X}] 0x{a:012X} {m} + 0x{off:X}")
