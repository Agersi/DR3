"""Capture DiRT Rally 2.0 UDP telemetry packets for offline analysis.

The game emits its motion-platform / telemetry stream to the port configured in
%userprofile%/Documents/My Games/DiRT Rally 2.0/hardwaresettings/
hardware_settings_config.xml (<udp ... port="NNNN" />). This tool listens on
that port, stores every datagram with a timestamp, and prints live stats so a
capture session can be monitored while driving.

Usage:
    python tools\\capture_dr2_udp.py [--port 20777] [--out build/dr2_udp_captures]
                                    [--duration SECONDS] [--tag NAME]

    --duration 0 (default) = run until Ctrl+C.
    --tag        optional session label, prefixed to the capture file name.

Output:
    <out>/<tag|timestamp>.jsonl   one JSON object per packet:
                                  {t, n, len, hex}
    <out>/<tag|timestamp>.stats.json  summary written at shutdown
"""

import argparse
import json
import signal
import socket
import sys
import time
from collections import Counter
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=20777)
    ap.add_argument("--out", type=Path, default=Path("build/dr2_udp_captures"))
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--tag", default="", help="session label for file names")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"{args.tag or time.strftime('%Y%m%d-%H%M%S')}"
    jsonl_path = args.out / f"{stem}.jsonl"
    stats_path = args.out / f"{stem}.stats.json"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", args.port))
    except OSError as e:
        print(f"cannot bind port {args.port}: {e}", file=sys.stderr)
        return 2
    sock.settimeout(0.5)

    stop = {"flag": False}

    def _sig(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGINT, _sig)

    t0 = time.time()
    n = 0
    sizes = Counter()
    senders = Counter()
    first_packet_hex = None
    last_packet_hex = None
    print(f"listening on 0.0.0.0:{args.port} -> {jsonl_path}")
    print("drive a stage, then Ctrl+C to stop")

    with jsonl_path.open("w", encoding="utf-8") as out:
        while not stop["flag"]:
            if args.duration and time.time() - t0 >= args.duration:
                break
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            n += 1
            now = time.time()
            sizes[len(data)] += 1
            senders[f"{addr[0]}:{addr[1]}"] += 1
            hx = data.hex()
            if first_packet_hex is None:
                first_packet_hex = hx
            last_packet_hex = hx
            out.write(json.dumps({"t": round(now - t0, 3), "n": n,
                                  "len": len(data), "hex": hx}) + "\n")
            if n % 250 == 0 or n <= 3:
                print(f"  pkt {n} at {now - t0:7.1f}s  len={len(data)}  from {addr[0]}:{addr[1]}")

    dt = time.time() - t0
    stats = {
        "port": args.port,
        "tag": args.tag,
        "started_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t0)),
        "duration_s": round(dt, 1),
        "packets": n,
        "rate_hz": round(n / dt, 2) if dt > 0 else 0,
        "sizes": dict(sizes.most_common()),
        "senders": dict(senders),
        "first_packet_hex": first_packet_hex,
        "last_packet_hex": last_packet_hex,
    }
    stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"\nstopped after {dt:.1f}s: {n} packets ({stats['rate_hz']} Hz)")
    print(f"sizes: {dict(sizes.most_common())}")
    print(f"senders: {dict(senders)}")
    print(f"wrote {jsonl_path.name} + {stats_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
