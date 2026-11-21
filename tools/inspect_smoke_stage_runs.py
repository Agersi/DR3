"""List stage_runs rows for a vehicle id from a SQLite DB (smoke-test triage).

Usage: python tools/inspect_smoke_stage_runs.py <db_path> [vehicle_id]
"""
import sqlite3
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    con = sqlite3.connect(sys.argv[1])
    vid = sys.argv[2] if len(sys.argv) > 2 else '565'
    cols = ('username, event_id, location_id, track_model_id, vehicle_id, '
            'time_ms, player_finished, distance_m')
    for row in con.execute(
            f'select {cols} from stage_runs where vehicle_id={int(vid)} order by id'):
        print(row)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
