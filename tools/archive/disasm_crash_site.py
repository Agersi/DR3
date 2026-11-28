"""Disassemble around a crash RVA in dirtrally2.exe (no symbols needed).

Parses the PE section table, maps the RVA to file bytes, walks backwards
for a function prologue (endbr64 / push rbp / sub rsp), and disassembles
from there through the faulting instruction.

Run: python tools\\disasm_crash_site.py <path-to-exe> <rva-hex> [context-bytes]
"""
import struct
import sys
from pathlib import Path

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from capstone import Cs, CS_ARCH_X86, CS_MODE_64  # noqa: E402


def load_sections(data: bytes):
    sig = data[:2]
    assert sig == b"MZ", "not a PE"
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe_off:pe_off + 4] == b"PE\0\0"
    coff = pe_off + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    sec_off = coff + 20 + opt_size
    secs = []
    for i in range(nsec):
        e = sec_off + i * 40
        name = data[e:e + 8].split(b"\0")[0].decode("ascii", "replace")
        vsize, vaddr, rawsize, rawoff = struct.unpack_from("<IIII", data, e + 8)
        secs.append((name, vaddr, vsize, rawoff, rawsize))
    return secs


def rva_to_file(rva: int, secs):
    for name, vaddr, vsize, rawoff, rawsize in secs:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return rawoff + (rva - vaddr)
    return None


def find_prologue(data: bytes, off: int, limit: int = 4096) -> int:
    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    best = None
    p = off
    while p > off - limit and p >= 16:
        q = p - 16
        insns = list(cs.disasm(bytes(data[q:p]), 0))
        for i in range(len(insns) - 1, -1, -1):
            mnem = insns[i].mnemonic
            if mnem == "endbr64" and i + 1 < len(insns):
                nxt = insns[i + 1].mnemonic
                if nxt in ("push", "sub", "mov"):
                    best = q
                    break
        p -= 8
    return best if best is not None else max(off - 256, 0)


def main() -> None:
    exe = Path(sys.argv[1])
    rva = int(sys.argv[2], 16)
    ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 320

    data = exe.read_bytes()
    secs = load_sections(data)
    foff = rva_to_file(rva, secs)
    if foff is None:
        print(f"RVA 0x{rva:X} not in any section")
        sys.exit(1)

    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    cs.detail = True

    start = find_prologue(data, foff)
    end = min(foff + ctx, len(data))
    chunk = bytes(data[start:end])
    base_rva = None
    for name, vaddr, vsize, rawoff, rawsize in secs:
        if rawoff <= start < rawoff + max(rawsize, 1):
            base_rva = vaddr + (start - rawoff)
            break

    print(f"exe={exe.name} crash RVA=0x{rva:X}")
    print(f"function prologue at file 0x{start:X} (RVA ~0x{base_rva:X})")
    fault_addr = base_rva + (foff - start)
    for insn in cs.disasm(chunk, base_rva):
        mark = " <== FAULT" if insn.address == fault_addr else ""
        print(f"  0x{insn.address:08X}  {insn.mnemonic:<8s} {insn.op_str}{mark}")


if __name__ == "__main__":
    main()
