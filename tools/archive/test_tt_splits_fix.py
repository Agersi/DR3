"""Verify TT split storage + best_times channel-inversion fixes end-to-end.

Boots the web app against a scratch SQLite DB and replays the two channels a
time trial posts (stage-detail snapshot + PostTime submit) in BOTH arrival
orders, asserting:
  T1 channel inversion (snapshot first, 1 ms skew): one best row, compound
     resolved, ghost + splits both present; one stage_runs row with splits;
  T2 normal order (PostTime first): placeholder adopted, payload kept;
  T3 old best + reverse order: write-time replace happens, the orphaned
     placeholder is folded in by the boot backfill on second boot;
  T4 slower run within tolerance: never touches the stored best, no orphan;
  T5 TT stage_runs disambiguation: back-to-back repeats keep separate rows,
     identical retransmits still collapse;
  T6 non-TT collapse unchanged: same challenge slot collapses, richer wins.

Plus a client-side check that App._fix_stage_detail_splits grabs the stashed
PostTime splits at emit time (the fix for TT stage_runs rows missing splits).

Usage: tools/test_tt_splits_fix.py [db_path]
       (default: <project root>/build/tt_splits_fix_test.db, wiped each run)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WEB = ROOT / 'dr3' / 'web'
sys.path.insert(0, str(WEB))

SPLITS_A = [12.5, 24.7, 38.9, 51.2, 63.4]
SPLITS_B = [12.1, 25.0, 39.5, 52.8, 66.1]
GHOST_A = 'Z2hvc3QtYS1ibG9i'


def sd_payload(time_s, *, track=583, car=575, compound=6, splits=None,
               challenge_id=0, ct=-1, cond=1):
    """StageEnded-snapshot-shaped /api/game/stage-detail payload (post-fix
    client: TT records carry the emit-time grabbed splits)."""
    p = {
        'challenge_id': challenge_id, 'event_index': 0, 'stage_index': 0,
        'challenge_type': ct,
        'track_id': track + 100, 'track_model_id': track, 'location_id': 4,
        'vehicle_class_id': 74, 'vehicle_id': car,
        'player_finished': True, 'player_end_status': 0, 'race_position': 1,
        'stage_time_seconds': time_s,
        'distance_driven': 2500,
        'using_wheel': True, 'using_assists': False,
        'is_hardcore_damage_enabled': True, 'is_any_assists_enabled': False,
        'tyre_compound_id': compound, 'tyre_wear_state': 0.4,
        'stage_conditions_id': cond, 'weather_preset_id': 1,
        'library_id': 'lib_direct_input', 'device_hardware_id': 'ABC-DEF',
        'driver_name': 'Simucube 2 Pro',
        'g_separate_days_played_count': 874,
        'g_total_seconds_played': 7842935, 'g_session_count': 1829,
    }
    if splits:
        p['splits'] = splits
    return p


def tt_payload(time_ms, *, track=583, car=575, ghost=GHOST_A, splits=None):
    """TimeTrial.PostTime-shaped /api/game/time-trial-submit payload (always
    carries the run's ghost; TT runs carry measured splits)."""
    p = {
        'vehicle_class_id': 74, 'track_model_id': track, 'conditions_id': 1,
        'category': 0, 'vehicle_id': car, 'livery_id': 3494,
        'stage_time_ms': time_ms, 'nationality_id': 31,
        'using_wheel': True, 'using_assists': False,
        'ghost_data_b64': ghost,
    }
    if splits:
        p['splits'] = splits
    return p


def main() -> int:
    db_path = (Path(sys.argv[1]) if len(sys.argv) > 1
               else ROOT / 'build' / 'tt_splits_fix_test.db')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    os.environ['SECRET_KEY'] = 'tt-fix-test-key'
    os.environ['DATABASE_URL'] = f'sqlite:///{db_path}'
    os.environ.setdefault('DR3_DISABLE_CSRF', '1')

    from app import create_app
    from app.db import db
    from app.models import User, GameToken, TimeTrialEntry, StageRun

    app = create_app()
    with app.app_context():
        user = User(username='ttfixer', email='ttfixer@example.com',
                    password_hash='x' * 64, salt='s' * 32,
                    display_name='TT Fixer')
        db.session.add(user)
        db.session.commit()
        db.session.add(GameToken(user_id=user.id, token='dr3_testtoken'))
        db.session.commit()

    client = app.test_client()
    hdr = {'Authorization': 'Bearer dr3_testtoken'}

    def post(path: str, payload: dict) -> None:
        r = client.post(path, json=payload, headers=hdr)
        assert r.status_code == 200 and r.get_json().get('ok'), \
            f'{path} -> {r.status_code} {r.get_data(as_text=True)[:200]}'

    def best_rows(track: int, car: int):
        with app.app_context():
            return db.session.query(TimeTrialEntry).filter_by(
                track=str(track), vehicle_id=car).all()

    def run_rows(track: int, car: int):
        with app.app_context():
            return db.session.query(StageRun).filter_by(
                track_model_id=track, vehicle_id=car).order_by(StageRun.id).all()

    checks = []

    # T1 — observed inversion: stage-detail first (135877), PostTime second
    # (135876, 1 ms lower): must land on ONE row with compound + ghost + splits.
    post('/api/game/stage-detail', sd_payload(135.877, splits=SPLITS_A))
    post('/api/game/time-trial-submit', tt_payload(135876, splits=SPLITS_B))
    rows = best_rows(583, 575)
    r = run_rows(583, 575)
    checks.append(('T1 inversion: one best row', len(rows) == 1))
    if rows:
        b = rows[0]
        checks.append(('T1 compound resolved + fastest ms kept',
                       b.tyre_compound == 6 and b.stage_time_ms == 135876))
        checks.append(('T1 ghost + splits both present',
                       bool(b.ghost_data_b64) and bool(b.splits_json)))
    checks.append(('T1 one stage_runs row with splits',
                   len(r) == 1 and bool(r[0].splits_json if r else None)))

    # T2 — normal order: PostTime first, snapshot adopts the placeholder.
    post('/api/game/time-trial-submit',
         tt_payload(140900, track=583, car=576, splits=SPLITS_B))
    post('/api/game/stage-detail', sd_payload(140.901, track=583, car=576,
                                              splits=SPLITS_A))
    rows = best_rows(583, 576)
    checks.append(('T2 normal order: one row, placeholder adopted',
                   len(rows) == 1 and rows[0].tyre_compound == 6
                   and rows[0].stage_time_ms == 140900
                   and bool(rows[0].ghost_data_b64)
                   and bool(rows[0].splits_json)))

    # T3 — old best exists, reverse order: write-time replace + boot-backfill
    # fold of the orphaned placeholder.
    with app.app_context():
        db.session.add(TimeTrialEntry(
            vclass='74', track='583', conditions='1', category=0,
            username='ttfixer', stage_time_ms=145000, vehicle_id=577,
            livery_id=1, tyre_compound=6, using_wheel=True,
            using_assists=False, ghost_data_b64='Z2hvc3QtZW9sZA=='))
        db.session.commit()
    post('/api/game/time-trial-submit',
         tt_payload(140900, track=583, car=577, splits=SPLITS_B))
    rows = best_rows(583, 577)
    checks.append(('T3 placeholder filed beside old best', len(rows) == 2))
    post('/api/game/stage-detail', sd_payload(140.901, track=583, car=577,
                                              splits=SPLITS_A))
    rows = best_rows(583, 577)
    real = [x for x in rows if x.tyre_compound == 6]
    checks.append(('T3 write-time replace onto old best',
                   len(rows) == 2 and len(real) == 1
                   and real[0].stage_time_ms == 140901))
    create_app()  # second boot — the backfill folds the orphan in
    rows = best_rows(583, 577)
    checks.append(('T3 backfill fold: one row, fastest ms + ghost',
                   len(rows) == 1 and rows[0].stage_time_ms == 140900
                   and bool(rows[0].ghost_data_b64)
                   and bool(rows[0].splits_json)))

    # T4 — slower run within tolerance: no orphan, best untouched.
    with app.app_context():
        db.session.add(TimeTrialEntry(
            vclass='74', track='583', conditions='1', category=0,
            username='ttfixer', stage_time_ms=135000, vehicle_id=578,
            livery_id=1, tyre_compound=6, using_wheel=True,
            using_assists=False, ghost_data_b64='Z2hvc3QtdDRvbGQ='))
        db.session.commit()
    post('/api/game/time-trial-submit', tt_payload(135876, track=583, car=578))
    post('/api/game/stage-detail', sd_payload(135.877, track=583, car=578,
                                              splits=SPLITS_A))
    rows = best_rows(583, 578)
    checks.append(('T4 slower run: best untouched, no orphan',
                   len(rows) == 1 and rows[0].stage_time_ms == 135000
                   and rows[0].ghost_data_b64 == 'Z2hvc3QtdDRvbGQ='))

    # T5 — TT stage_runs disambiguation (challenge_id=0 for every TT run).
    post('/api/game/stage-detail', sd_payload(150.123, track=450, car=600))
    post('/api/game/stage-detail', sd_payload(155.456, track=450, car=600))
    rows = run_rows(450, 600)
    checks.append(('T5 back-to-back TT repeats: two rows', len(rows) == 2))
    post('/api/game/stage-detail', sd_payload(150.123, track=450, car=600))
    rows = run_rows(450, 600)
    checks.append(('T5 identical retransmit collapses', len(rows) == 2))
    post('/api/game/stage-detail', sd_payload(150.123, track=450, car=601))
    rows = run_rows(450, 601)
    checks.append(('T5 same time, different car: own row', len(rows) == 1))

    # T6 — non-TT collapse unchanged: same slot collapses, richer wins.
    post('/api/game/stage-detail', {
        'challenge_id': 999, 'event_index': 1, 'stage_index': 2,
        'vehicle_id': 602, 'stage_time_ms': 160000, 'distance_driven': 3000,
    })
    post('/api/game/stage-detail', {
        'challenge_id': 999, 'event_index': 1, 'stage_index': 2,
        'challenge_type': 1, 'track_model_id': 623, 'vehicle_id': 602,
        'stage_time_ms': 160000, 'distance_driven': 3000,
        'library_id': 'lib_wheel', 'cam_dash_m': 12.5,
    })
    rows = run_rows(623, 602)
    checks.append(('T6 official slot collapse: one richer row',
                   len(rows) == 1 and rows[0].input_library == 'lib_wheel'))

    # Client side — the emit grabs the stashed PostTime splits (TT fix).
    sys.path.insert(0, str(ROOT / 'dr3'))
    from dr2server.httpd import App as Dr3App
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        srv = Dr3App(data_root=tdp / 'data', capture_root=tdp / 'cap')
        splits = [1.0, 2.5, 4.2]
        srv.dispatcher._pending_posttime_splits[575] = (time.time(), splits)
        record = {'vehicle_id': 575}
        srv._fix_stage_detail_splits(record)
        checks.append(('client: emit grabs stashed TT splits',
                       record.get('splits') == splits))
        record2 = {'vehicle_id': 575, 'splits': [9.0]}
        srv._fix_stage_detail_splits(record2)
        checks.append(('client: existing splits pass through',
                       record2['splits'] == [9.0]))

    failed = 0
    for name, ok in checks:
        print(f'[{"ok" if ok else "FAIL"}] {name}')
        failed += 0 if ok else 1
    print('ALL TT SPLIT CHECKS PASSED' if not failed
          else f'{failed} TT SPLIT CHECK(S) FAILED')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
