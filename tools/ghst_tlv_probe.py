#!/usr/bin/env python3
"""GHST header TLV parser probe.

The GHST payload (after 'GHST' + u32 size) appears to contain a run of
[tag][len][payload] records before the sample stream starts. This tool tries
every candidate start offset, walks the TLV sequence as far as it parses
cleanly, and prints the tag/len/value table so the layout can be read off.

Usage: ghst_tlv_probe.py CAPTURE_JSON [CAPTURE_JSON ...]
"""
import base64
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "dr3"))


def load_ghst(path):
    d = json.load(open(path, encoding="utf-8"))
    body = d["decoded_body"]
    blob = base64.b64decode(body["GhostData"]["blob_base64"])
    data = zlib.decompress(blob[8:])
    return data, body


def fmt_val(payload):
    if len(payload) == 4:
        u32 = struct.unpack_from("<I", payload)[0]
        f32 = struct.unpack_from("<f", payload)[0]
        bits = f"{payload.hex(' ')}"
        out = f"u32={u32} f32={f32:.6g}"
        if 0 < abs(f32) < 1e5:
            out += f" [plausible-float]"
        return bits + " | " + out
    if len(payload) == 8:
        u64 = struct.unpack_from("<Q", payload)[0]
        f64 = struct.unpack_from("<d", payload)[0]
        return f"{payload.hex(' ')} | u64={u64} f64={f64:.6g}"
    if len(payload) == 2:
        return f"{payload.hex(' ')} | u16={struct.unpack_from('<H', payload)[0]}"
    if len(payload) == 1:
        return f"{payload[0]:02x} | u8={payload[0]}"
    return payload[:16].hex(" ") + ("..." if len(payload) > 16 else "")


def walk_tlv(data, start):
    off = start
    recs = []
    while off + 2 <= len(data):
        tag = data[off]
        ln = data[off + 1]
        if ln == 0 or off + 2 + ln > len(data):
            break
        payload = data[off + 2:off + 2 + ln]
        recs.append((tag, ln, payload))
        off += 2 + ln
    return recs, off


def main():
    for path in sys.argv[1:]:
        data, body = load_ghst(path)
        N = len(data)
        st = float(body["StageTime"])
        print("=" * 78)
        print("%s  track=%s st=%.3f N=%d" %
              (path.split("\\")[-1], body["TrackModelId"], st, N))
        best = None
        for start in range(8, min(256, N - 16)):
            recs, end = walk_tlv(data, start)
            # clean parse should reach at least offset 150 and have >= 4 recs
            if len(recs) >= 4 and end >= 150:
                if best is None or (end, len(recs)) > (best[1], best[2]):
                    best = (start, end, len(recs), recs)
        if not best:
            print("  no clean TLV walk found")
            continue
        start, end, nrec, recs = best
        print(f"  start={start} end={end} recs={nrec}")
        for tag, ln, payload in recs[:40]:
            print(f"    tag=0x{tag:02x} len={ln:3d}  {fmt_val(payload)}")


if __name__ == "__main__":
    main()
