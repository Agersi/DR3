#!/usr/bin/env python3
"""Stamp submitted_at on terminal-damage stage entries that lack one.

The home page's "Days without a Terminal Damage" card only counts club-result
stage entries with a non-zero race_status AND a non-null submitted_at. DNF
close-out markers (game_api mark_dnf branch) flagged race_status but never
wrote submitted_at, so real wrecks were invisible to the card. New markers
carry the timestamp since the fix; this backfill repairs the legacy ones by
matching each NULL entry against the driver's own DNF stage_runs rows
(player_finished = 0) for the same event and using the run's received_at as
the wreck time.

Match rules (strictest first):
  1. same username + event_id + flat_stage_index (both non-null) -> unique
  2. exactly one DNF stage_runs row for that username + event_id -> it
  3. otherwise ambiguous / no candidate -> reported, left untouched

Cron close-out DNFs (round expiry, events_generator) have no matching DNF run
row and are therefore never stamped — they are not terminal damage.

Dry run by default; pass --apply to commit. Run on the VPS:
  /opt/dr3/.venv/bin/python backfill_terminal_damage_ts.py [--apply]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def connect():
    env = {}
    for line in open(os.path.join(HERE, 'web', '.env')):
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k] = v
    m = re.match(r'mysql\+pymysql://([^:]+):([^@]+)@([^:/]+):?(\d+)*/(\S+)',
                 env['DATABASE_URL'])
    import pymysql
    return pymysql.connect(host=m.group(3), port=int(m.group(4) or 3306),
                           user=m.group(1), password=m.group(2),
                           database=m.group(5).split('?')[0])


def main():
    apply = '--apply' in sys.argv[1:]
    conn = connect()
    cur = conn.cursor()

    targets = []  # (result_id, username, event_id, flat, rs)
    cur.execute('SELECT id, username, event_id, stages_json FROM club_results')
    for rid, uname, eid, sj in cur.fetchall():
        for i, s in enumerate(json.loads(sj or '[]')):
            rs = int(s.get('race_status', 0) or 0)
            # Any non-zero status is a terminal close-out (the game has sent
            # 4, 5 and 6); cron round-expiry DNFs are excluded by the run-row
            # match below, not by the status value.
            if rs == 0:
                continue
            if s.get('submitted_at'):
                continue
            targets.append((rid, uname, eid, i, rs))

    stamped = skipped = 0
    for rid, uname, eid, flat, rs in targets:
        cur.execute(
            'SELECT id, flat_stage_index, received_at FROM stage_runs '
            'WHERE username = %s AND event_id = %s AND player_finished = 0',
            (uname, eid))
        runs = cur.fetchall()
        pick = None
        if any(r[1] == flat for r in runs):
            cands = [r for r in runs if r[1] == flat]
            if len(cands) == 1:
                pick = cands[0]
        elif len(runs) == 1:
            pick = runs[0]
        if pick is None or pick[2] is None:
            skipped += 1
            print(f'  result{rid} {uname} evt={eid} flat={flat} rs={rs}: '
                  f'{"no DNF run row" if not runs else "ambiguous " + str(len(runs)) + " DNF run rows"} — skip')
            continue
        ts = pick[2].isoformat()
        print(f'  result{rid} {uname} evt={eid} flat={flat} rs={rs} '
              f'-> sr{pick[0]} received={ts}')
        if apply:
            cur.execute('SELECT stages_json FROM club_results WHERE id = %s', (rid,))
            sj = cur.fetchone()[0]
            stages = json.loads(sj or '[]')
            stages[flat]['submitted_at'] = ts
            cur.execute('UPDATE club_results SET stages_json = %s WHERE id = %s',
                        (json.dumps(stages), rid))
        stamped += 1

    print(f'\n{"applied" if apply else "DRY RUN"} — {stamped} stamped, '
          f'{skipped} skipped' + ('' if apply else ' (re-run with --apply to commit)'))
    if apply:
        conn.commit()
    conn.close()


if __name__ == '__main__':
    main()
