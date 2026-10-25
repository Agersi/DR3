import json, glob, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for f in sorted(glob.glob(r"C:\Users\Agersi\AppData\Roaming\DR3\captures\20261024-033715*.json")):
    d = json.load(open(f, encoding="utf-8"))
    fn = (d.get("headers") or {}).get("X-EgoNet-Function", "?")
    if fn != "Clubs.GetClubs":
        continue
    body = (d.get("response") or {}).get("body") or {}
    print("TOP KEYS:", list(body.keys()))
    for c in body.get("Clubs", []):
        print("CLUB:", {k: v for k, v in c.items() if k in ("Id","ClubId","Name","CreatorName","AmountOfEvents","State")})
    for ch in body.get("Challenges", []):
        print(f"CHAL id={ch.get('ChallengeID')} club={ch.get('ClubId')} name={ch.get('Name')!r} events={[e.get('EventId') for e in ch.get('Events', [])]}")
