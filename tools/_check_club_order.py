"""Read-only: inspect live championship windows for the club-ordering check."""
import json
import sys

sys.path.insert(0, r"D:\CODE\DR2")
from tools.vps import Vps  # noqa: E402


def main():
    vps = Vps()
    try:
        e = {}
        with open(r"D:\CODE\DR2\.env", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip()
        user, pw, db = e["DR3_DB_USER"], e["DR3_DB_PASSWORD"], e["DR3_DB_NAME"]
        q = (
            "SELECT c.name AS club, e.id, e.start_time, e.end_time, "
            "e.settings_json, e.legs_json, NOW() "
            "FROM events e JOIN clubs c ON c.id = e.club_id "
            "WHERE e.active = 1"
        )
        cmd = (f'mysql -u {user} -p{pw} {db} -N -B -e "{q}"')
        rc, out, err = vps.run(cmd, timeout=60)
        if rc != 0:
            print("query failed:", err)
            return
        now_line = None
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            club, eid, start, end, settings, legs, now = parts[:7]
            now_line = now
            try:
                st = json.loads(settings)
            except Exception:
                st = {}
            try:
                legs_j = json.loads(legs)
            except Exception:
                legs_j = []
            print(f"=== {club} | {eid} | mode={st.get('mode')}")
            print(f"    start={start}  end={end}  (now={now_line})")
            for i, leg in enumerate(legs_j):
                dur = leg.get("duration") or {}
                print(f"    leg{i}: {leg.get('location')} "
                      f"dur={dur.get('days',0)}d{dur.get('hours',0)}h{dur.get('mins',0)}m "
                      f"stages={len(leg.get('stages') or [])}")
        print()
        print("server now:", now_line)
    finally:
        vps.close()


if __name__ == "__main__":
    main()
