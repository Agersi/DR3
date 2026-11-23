#!/usr/bin/env python3
"""Per-user stage_runs split: all rows vs rows carrying a distance.

Since the 2026-11-23 fix BOTH /drivers and /leaderboard (distance mode) count
every recorded run as a stage (DNF markers included, contributing 0 distance),
so driven_all is what both pages show; with_distance is informational only
(rows whose distance_m fed the distance sum). Usage:
check_stage_counts_drivers_vs_leaderboard.py [ENV_FILE]
"""
import re
import sys

env_file = sys.argv[1] if len(sys.argv) > 1 else '/opt/dr3/web/.env'
env = {}
for line in open(env_file, encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

import pymysql  # noqa: E402

p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             env['DATABASE_URL'])
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split('?')[0])
cur = conn.cursor()

cur.execute("""
SELECT username,
       COUNT(*) AS driven_all,
       SUM(distance_m IS NOT NULL AND distance_m > 0) AS with_distance,
       SUM(distance_m IS NULL OR distance_m <= 0) AS dnf_or_zero
FROM stage_runs
GROUP BY username
HAVING dnf_or_zero > 0
ORDER BY dnf_or_zero DESC, username
""")
rows = cur.fetchall()

cur.execute("SELECT COUNT(*), SUM(distance_m IS NOT NULL AND distance_m > 0) FROM stage_runs")
n_all, n_dist = cur.fetchone()
total_all, total_dist, total_dnf = (int(n_all), int(n_dist or 0),
                                    int(n_all) - int(n_dist or 0))

print(f"TOTAL: stages counted by both pages={total_all}"
      f"  rows-with-distance={total_dist}  DNF/zero-distance={total_dnf}")
print(f"DRIVERS WITH DNFs: {len(rows)}")
for u, all_n, dist_n, dnf in rows:
    print(f"  {u}: stages={all_n}  with_distance={dist_n}  dnf={dnf}")

# Live page comparison: /drivers "Stages driven" vs /leaderboard?mode=distance
# "Stages" (both render driver-link then the stages <td class="num">).
import re as _re  # noqa: E402
import urllib.request  # noqa: E402

base = sys.argv[2] if len(sys.argv) > 2 else 'https://byagersi.com/rally'
PAT = _re.compile(
    r'class="driver-link"[^>]*>([^<]+)</a></td>\s*<td class="num">(\d+)</td>')

def page_stages(url):
    txt = urllib.request.urlopen(url, timeout=20).read().decode('utf-8')
    return dict(PAT.findall(txt))

drv = page_stages(f'{base}/drivers')
lb = page_stages(f'{base}/leaderboard?mode=distance')
mismatch = 0
for name in sorted(set(drv) | set(lb)):
    a, b = drv.get(name), lb.get(name, '0')
    if a != b:
        mismatch += 1
        print(f'  MISMATCH {name}: /drivers={a} leaderboard={b}')
print(f'LIVE PAGES: drivers rows={len(drv)} distance-board rows={len(lb)}'
      f' mismatches={mismatch}')
