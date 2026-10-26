"""Byte-level field analysis of TuningSetup payloads from a hex dump file
(lines shaped: '<label> stage[N] usize=M: <hex>'). Groups by label, prints
per-pair differing byte ranges (with values) and the byte ranges constant
across every stage of the group.

Payload layout established 2026-10-26 (see docs/PLAN.md item #17): u32(3),
u32(1), 2 zero bytes, 6-byte setup-ID region (bytes 10-15; rally setups end
'6772', RX setups end '746d'), dynamic per-stage byte 17, then float
parameter regions.

Usage:
  python tuning_field_analysis.py build\\tuning_hex.txt
"""
import re
import sys


def parse(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        m = re.match(r"^(\S.*?) stage\[(\d+)\] usize=\d+: ([0-9a-f]+)", line.strip())
        if m:
            out.setdefault(m.group(1), []).append(bytes.fromhex(m.group(3)))
    return {k: v for k, v in out.items() if v}


def ranges(idx):
    out, start = [], None
    for i in idx:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append((start, prev))
            start = prev = i
    if start is not None:
        out.append((start, prev))
    return out


def main():
    groups = parse(sys.argv[1] if len(sys.argv) > 1 else r"build\tuning_hex.txt")
    for label, blobs in groups.items():
        print(f"=== {label} ({len(blobs)} payloads) ===")
        for i in range(len(blobs)):
            for j in range(i + 1, len(blobs)):
                a, b = blobs[i], blobs[j]
                n = min(len(a), len(b))
                idx = [k for k in range(n) if a[k] != b[k]]
                extra = abs(len(a) - len(b))
                print(f"  stage[{i}] vs stage[{j}]: n_diff={len(idx) + extra} ranges={ranges(idx)}")
                if j == i + 1:
                    for s, e in ranges(idx):
                        print(f"    bytes {s:3d}-{e:3d}: {a[s:e+1].hex()} -> {b[s:e+1].hex()}")
        n = min(len(x) for x in blobs)
        const = [i for i in range(n) if len({x[i] for x in blobs}) == 1]
        print(f"  constant byte ranges: {ranges(const)}")
        print()


if __name__ == "__main__":
    main()
