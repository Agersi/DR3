#!/bin/bash
# Per-stage TuningSetup blob summary (raw length + sha1[:8]) from the production
# DB, for one or more event ids (default: every event that has result rows).
# Blobs are read from club_stage_states (post-migration); a legacy-inline
# fallback covers pre-migration rows. Run via:
#   python tools\vps.py --run-script tools\db_inspect_tuning_blobs.sh
#   python tools\vps.py --run-script tools\db_inspect_tuning_blobs.sh evt-xxxx [evt-yyyy ...]
cd /opt/dr3/web
/opt/dr3/.venv/bin/python - "$@" <<'EOF'
import base64, hashlib, json, re, sys
env = {}
for line in open('.env'):
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k] = v
m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)', env['DATABASE_URL'])
user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4) or 3306), m.group(5).split('?')[0]
import pymysql
conn = pymysql.connect(host=host, port=port, user=user, password=pw, database=dbname)
cur = conn.cursor()

def blobinfo(b64):
    if not b64:
        return 'EMPTY'
    try:
        raw = base64.b64decode(b64)
        h = hashlib.sha1(raw).hexdigest()[:8]
        return f'b64len={len(b64)} rawlen={len(raw)} sha1={h}'
    except Exception as e:
        return f'INVALID({e}) b64len={len(b64)}'

if len(sys.argv) > 1:
    eids = sys.argv[1:]
else:
    cur.execute("SELECT DISTINCT event_id FROM club_results ORDER BY event_id")
    eids = [r[0] for r in cur.fetchall()]

for eid in eids:
    cur.execute("SELECT username, vehicle_id, stages_json FROM club_results WHERE event_id=%s", (eid,))
    rows = cur.fetchall()
    print(f'===== {eid} ({len(rows)} result rows) =====')
    for uname, veh, stages in rows:
        st = json.loads(stages or '[]')
        # Per-stage heavy payloads from the side table (post-migration).
        cur.execute("""SELECT flat_stage_index, tuning_setup_b64
                       FROM club_stage_states WHERE event_id=%s AND username=%s""",
                    (eid, uname))
        side = {idx: b64 or '' for idx, b64 in cur.fetchall()}
        print(f'-- user={uname} veh={veh}')
        for i, s in enumerate(st):
            if not s:
                print(f'   stage[{i}]: (empty)')
                continue
            t = blobinfo(side.get(i) or s.get('tuning_setup_b64', ''))
            print(f"   stage[{i}]: time={s.get('time_ms')} veh={s.get('vehicle_id')} tyre={s.get('tyre_compound')}/{s.get('tyres_remaining')} tuning={t} submitted={s.get('submitted_at')}")
        for i in sorted(set(side) - set(range(len(st)))):
            print(f'   pending[{i}]: tuning={blobinfo(side[i])}')
    print()
EOF
