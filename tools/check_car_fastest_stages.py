#!/usr/bin/env python3
"""Diagnose the vehicle page's "fastest stage" tables for one track.

The car page (app/cars_data.py fastest_stages_by_location) ranks finished
stage_runs rows by their TOTAL time, while the site leaderboard ranks
best_times rows — which for rallycross tracks are single LAP times. This tool
dumps both sources for a track from the live DB and shows which car wins
overall / in-class under each, so total-vs-lap mismatches are visible.

Usage:  check_car_fastest_stages.py [TRACK_MODEL_ID] [VEHICLE_ID]
        defaults: 641 (Bikernieki RX), 470 (Lancia Stratos)
Read-only.
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'dr3'))

from vps import Vps  # noqa: E402
from dr2server.game_data import TRACKS, VEHICLES, VEHICLE_CLASSES  # noqa: E402

TRACK_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 641
VEHICLE_ID = int(sys.argv[2]) if len(sys.argv) > 2 else 470

REMOTE = r'''
import os, re
env = {}
for line in open('/opt/dr3/web/.env', encoding='utf-8'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
p = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)",
             env['DATABASE_URL'])
import pymysql
conn = pymysql.connect(host=p.group(3), port=int(p.group(4) or 3306),
                       user=p.group(1), password=p.group(2),
                       database=p.group(5).split('?')[0])
cur = conn.cursor()
track = int(os.environ['TRACK_ID'])
print('SR')
cur.execute("SELECT id, username, vehicle_id, time_ms, player_finished,"
            " splits_json FROM stage_runs WHERE track_model_id=%s"
            " ORDER BY time_ms", (track,))
for r in cur.fetchall():
    print('\t'.join('' if x is None else str(x) for x in r))
print('BT')
cur.execute("SELECT username, vehicle_id, conditions, tyre_compound,"
            " stage_time_ms, run_id FROM best_times WHERE track=%s"
            " ORDER BY stage_time_ms", (str(track),))
for r in cur.fetchall():
    print('\t'.join('' if x is None else str(x) for x in r))
'''


def car_label(vid):
    v = VEHICLES.get(int(vid)) if vid not in ('', None) else None
    if not v:
        return f'car {vid}', '?'
    return v['name'], VEHICLE_CLASSES.get(v['class'], str(v['class']))


def main():
    tinfo = TRACKS.get(TRACK_ID, {})
    target_class = VEHICLES.get(VEHICLE_ID, {}).get('class')
    print(f'track {TRACK_ID} ({tinfo.get("name", "?")}, '
          f'location {tinfo.get("location_id", "?")})  |  '
          f'vehicle {VEHICLE_ID} = '
          f'{car_label(VEHICLE_ID)[0]} (class {target_class})')

    vps = Vps()
    chan = vps.client.get_transport().open_session()
    chan.exec_command('cd /opt/dr3/web && /opt/dr3/.venv/bin/python -')
    env_line = f'import os; os.environ["TRACK_ID"]="{TRACK_ID}"; '
    chan.sendall((env_line + REMOTE).encode())
    chan.shutdown_write()
    out = b''
    while not chan.exit_status_ready():
        if chan.recv_ready():
            out += chan.recv(65536)
        if chan.recv_stderr_ready():
            out += chan.recv_stderr(65536)
    rc = chan.recv_exit_status()
    vps.close()
    if rc:
        print(f'[remote exit {rc}]')
        print(out.decode('utf-8', 'replace'))
        return

    lines = out.decode('utf-8', 'replace').splitlines()
    stage_runs, best_times = [], []
    section = None
    for line in lines:
        if line in ('SR', 'BT'):
            section = line
            continue
        if not line.strip():
            continue
        parts = line.split('\t')
        (stage_runs if section == 'SR' else best_times).append(parts)

    print(f'\n=== stage_runs ({len(stage_runs)} rows, car page source; '
          f'time_ms = TOTAL race time) ===')
    for p in stage_runs:
        rid, user, vid, ms, fin, splits = (p + [''] * 6)[:6]
        laps = ''
        if splits:
            try:
                sv = json.loads(splits)
                laps = ' laps=[' + ', '.join(f'{s:.2f}' for s in sv[:12]) + ']'
            except ValueError:
                pass
        name, cls = car_label(vid)
        mark = '' if fin == '1' else '  [NOT FINISHED]'
        star = '  <== vehicle under test' if int(vid or -1) == VEHICLE_ID else ''
        print(f'  {int(ms):>8} ms  {user:<20} {name:<28} ({cls}){mark}{star}{laps}')

    print(f'\n=== best_times ({len(best_times)} rows, leaderboard source; '
          f'RX rows are single LAP times) ===')
    for p in best_times:
        user, vid, cond, comp, ms, run_id = (p + [''] * 6)[:6]
        name, cls = car_label(vid)
        star = '  <== vehicle under test' if int(vid or -1) == VEHICLE_ID else ''
        print(f'  {int(ms):>8} ms  {user:<20} {name:<28} ({cls}) '
              f'cond={cond} comp={comp} run_id={run_id or "-"}{star}')

    def winner(rows, field_ms, field_vid, finished_only):
        best = None
        for p in rows:
            if finished_only and p[4] != '1':
                continue
            ms = int(p[field_ms] or 0)
            if ms <= 0:
                continue
            vid = p[field_vid]
            if best is None or ms < best[0]:
                best = (ms, vid)
        return best

    def in_class_winner(rows, field_ms, field_vid, finished_only):
        best = None
        for p in rows:
            if finished_only and p[4] != '1':
                continue
            ms = int(p[field_ms] or 0)
            if ms <= 0:
                continue
            vid = p[field_vid]
            if VEHICLES.get(int(vid), {}).get('class') != target_class:
                continue
            if best is None or ms < best[0]:
                best = (ms, vid)
        return best

    print('\n=== who wins ===')
    for label, rows, fms, fvid, fin in (
            ('stage_runs totals (car page today)', stage_runs, 3, 2, True),
            ('best_times laps   (leaderboard view)', best_times, 4, 1, False)):
        o = winner(rows, fms, fvid, fin)
        c = in_class_winner(rows, fms, fvid, fin)
        fmt = lambda w: (f'{int(w[0])} ms by {car_label(w[1])[0]} '
                         f'({car_label(w[1])[1]})' if w else 'none')
        print(f'  {label}:')
        print(f'    overall : {fmt(o)}')
        print(f'    in-class: {fmt(c)}')


if __name__ == '__main__':
    main()
