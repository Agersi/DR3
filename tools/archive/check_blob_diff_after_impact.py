#!/usr/bin/env python3
"""Diff stored tuning blobs across consecutive stages around impact spikes.

For each (event, user) with a wheels_impact spike >= threshold, decompresses
the stored tuning_setup_b64 of the spike stage and its neighbours and prints
byte-level diffs vs the previous stage's blob — to test whether a puncture
(left over from the spike stage) leaves any trace in the blob the game sends
at the next stage's start (beyond the known per-stage byte 17 + spare count).

Run on the VPS:
    /opt/dr3/.venv/bin/python check_blob_diff_after_impact.py [--threshold N]
"""
import base64
import json
import os
import re
import struct
import sys
import zlib

THRESHOLD = 180
if "--threshold" in sys.argv:
    i = sys.argv.index("--threshold")
    THRESHOLD = int(sys.argv[i + 1])

with open("/opt/dr3/web/.env") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymysql  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             os.environ["DATABASE_URL"])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split("?")[0])
cur = conn.cursor()

cur.execute("""
    SELECT event_id, username, flat_stage_index, comp_damage_json, tuning_setup_b64
    FROM club_stage_states
    WHERE comp_damage_json IS NOT NULL
    ORDER BY event_id, username, flat_stage_index
""")
by_key = {}
spikes = []
for event_id, username, idx, dam, tb64 in cur.fetchall():
    try:
        d = json.loads(dam)
    except (ValueError, TypeError):
        continue
    by_key.setdefault((event_id, username), {})[idx] = (d, tb64)
    wi = d.get("wheels_impact", 0) or 0
    b = [(wi >> (8 * k)) & 0xFF for k in range(4)]
    if max(b) >= THRESHOLD:
        spikes.append((event_id, username, idx))

def payload_of(tb64):
    if not tb64:
        return None
    try:
        raw = base64.b64decode(tb64)
        if len(raw) < 16:
            return None
        size = struct.unpack_from("<I", raw, 8)[0]
        pl = zlib.decompress(raw[16:])
        return pl if len(pl) == size else None
    except Exception:
        return None

def f210(pl):
    if pl is None or len(pl) < 214:
        return None
    return struct.unpack_from("<f", pl, 210)[0]

seen = set()
for event_id, username, idx in spikes:
    stages = by_key[(event_id, username)]
    if idx not in stages or idx + 1 not in stages:
        continue
    key = (event_id, username, idx)
    if key in seen:
        continue
    seen.add(key)
    d_hi, tb_hi = stages[idx]
    d_nx, tb_nx = stages[idx + 1]
    pl_hi = payload_of(tb_hi)
    pl_nx = payload_of(tb_nx)
    wi = [(d_hi.get("wheels_impact", 0) or 0) >> (8 * k) & 0xFF for k in range(4)]
    print(f"\n=== {event_id} / {username} — spike stage {idx} impact={wi} ===")
    if pl_hi is None or pl_nx is None:
        print("  blob missing on one side; skip")
        continue
    diffs = [(k, pl_hi[k], pl_nx[k]) for k in range(min(len(pl_hi), len(pl_nx)))
             if pl_hi[k] != pl_nx[k]]
    print(f"  blob {idx} -> {idx + 1}: {len(diffs)} differing bytes")
    for k, a, b_ in diffs:
        note = ""
        if k == 17:
            note = " (per-stage byte)"
        if 210 <= k <= 213:
            note = f" (spares float {f210(pl_hi)} -> {f210(pl_nx)})"
        print(f"    off {k:3d}: {a:02x} -> {b_:02x}{note}")
