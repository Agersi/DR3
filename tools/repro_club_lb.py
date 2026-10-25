"""Repro/verify for backlog item 18: in-game club overview leaderboards.

Verifies the fixed _clubs_leaderboard chain against the live web API using
the token from the local DR3 client config (token never printed):

1. Club int IDs are pure functions of the club string id — identical no
   matter how the club list is ordered or extended (stale game caches keep
   resolving).
2. A stable ClubId resolves to that club's own active event and its real
   leaderboard entries.
3. An unknown/stale ClubId yields an empty board (no cross-club fallback).
"""
import json
import sys
from pathlib import Path

ROOT = Path(r"D:\CODE\DR2\dr3")
sys.path.insert(0, str(ROOT))

CONFIG = Path(r"C:\Users\Agersi\AppData\Roaming\DR3\config.json")


def build_map(clubs):
    from dr2server.dispatcher import _stable_int_id
    m = {}
    for wclub in clubs:
        club_str_id = wclub.get("id", "")
        m[_stable_int_id(club_str_id, base=2000)] = club_str_id
    return m


def trace(client, events, club_id_map, requested):
    """Mirror the fixed _clubs_leaderboard event resolution (no fallback)."""
    web_club_id = club_id_map.get(requested)
    event_id = None
    if web_club_id:
        for e in events:
            if e.get("club_id") == web_club_id and e.get("active"):
                event_id = e.get("id")
                break
    entries = []
    if event_id:
        entries = client.get_leaderboard(event_id) or []
    return web_club_id, event_id, entries


def main() -> None:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    api_url = cfg.get("api_url", "https://byagersi.com/rally")
    token = cfg.get("game_token")

    from dr2server.api_client import UpstreamClient
    from dr2server.dispatcher import _stable_int_id
    client = UpstreamClient(base_url=api_url, api_token=token)
    print(f"token-test -> user={client.test_token()!r}")

    data = client.get_clubs()
    clubs = data.get("clubs", [])
    events = data.get("events", [])
    print(f"get_clubs -> {len(clubs)} clubs, {len(events)} events")

    # 1. ID stability: shuffle + extend the club list, IDs must not change.
    base_map = build_map(clubs)
    shuffled = list(reversed(clubs))
    extended = clubs + [{"id": "club-new-tester", "name": "x"}] * 3
    assert build_map(shuffled) == base_map, "IDs changed with reorder!"
    ext_map = build_map(extended)
    for k, v in base_map.items():
        assert ext_map.get(k) == v, f"ID {k} shifted when a club was added!"
    print(f"PASS: IDs stable under reorder/extension; map={base_map}")

    # 2. Each club with an active event resolves to its own board.
    ok = True
    for wclub in clubs:
        cid = _stable_int_id(wclub.get("id", ""), base=2000)
        web_club_id, event_id, entries = trace(client, events, base_map, cid)
        have_event = any(e.get("club_id") == wclub.get("id") and e.get("active")
                         for e in events)
        status = "ok" if (event_id is not None) == have_event else "MISMATCH"
        if status != "ok":
            ok = False
        print(f"  {status} ClubId={cid} club={wclub.get('id')!r} "
              f"event={event_id!r} entries={len(entries)}")
        for en in entries:
            print(f"      user={en.get('username')!r} points={en.get('points')!r}")

    # 3. Stale/unknown ClubIds (old positional values from the 2026-10-24
    #    session) must yield an empty board, not another club's data.
    for stale in (26355, 74128, 41372):
        web_club_id, event_id, entries = trace(client, events, base_map, stale)
        print(f"  stale ClubId={stale} -> web_club_id={web_club_id!r} "
              f"event={event_id!r} entries={len(entries)} (expect 0)")
        if event_id is not None or entries:
            ok = False

    print("RESULT:", "ALL PASS" if ok else "FAILURES PRESENT")


if __name__ == "__main__":
    main()
