# DR3

DR3 is a self-hosted club server for **DiRT Rally 2.0**. 
DR3 replaces the official RaceNet/EgoNet backend with your own: players run a small local server on their PC, and clubs, championships, leaderboards and stage results are served by your site instead of Codemasters'.

DR3 has a proxy system to allow Daily/Weekly/Monthly challenges and TimeTrials to keep working using the RaceNet/EgoNet backend.

Live instance: <https://byagersi.com/rally>

## What it does

- **Clubs & championships** — create rally and rallycross events with a web
  builder: per-championship scoring (WRC, Group B, or Codemasters
  field-size formula), power-stage bonus points, Multi-Location Rally (one
  car-select, vehicle state carried between rallies) and Rally Season modes.
- **Stats** - Much better stats, compare sector times, tyre compounds, tyre wear and much more!
- **In-game client** — `DR3.exe` (Windows): manages the hosts redirect + TLS
  cert, runs the local proxy server, verifies your game token, and logs
  everything to a GUI with Main / Overlay / Logs tabs.
- **Streaming text files** — while the server runs, DR3 can write OBS/SimHub
  text files (club, event, stage name, stage counter, car, class, top-5
  leaderboard) for stream overlays.


## Getting the client

Download the latest `DR3-windows.zip` from <https://byagersi.com/rally>
(served at `/download/DR3-windows.zip`), extract it anywhere, run
`DR3.exe`, paste your game token and press **Start**. Then launch DiRT
Rally 2.0 via Steam.


## Building the client exe

```
cd dr3
python -m venv .venv-build && .venv-build\Scripts\activate
pip install pyinstaller cryptography
python build_exe.py          # -> dist/DR3-windows.zip
```