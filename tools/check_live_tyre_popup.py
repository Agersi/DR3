"""Trusted-click test for the leaderboard tyre damage popup via CDP.

Loads the live leaderboard, clicks the first tyre image with real mouse
events, then reports what the pinned .dmg-popup shows (title, weather +
compound line, car model vs no-data fallback) and that a second click on
the same tyre closes it again. Needs local Edge + system Python with
`websockets`.

Usage: check_live_tyre_popup.py [URL] [ROW]

ROW is the 0-based index of the board row whose first tyre image gets
clicked (default 0).
"""
import asyncio
import json
import os
import random
import subprocess
import sys
import urllib.request

import websockets

EDGE = next((p for p in (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
             if os.path.exists(p)), None)
PORT = random.randint(9300, 9499)
URL = sys.argv[1] if len(sys.argv) > 1 else "https://byagersi.com/rally/leaderboard"
ROW = int(sys.argv[2]) if len(sys.argv) > 2 else 0
TYRE_SEL = f"#lb-board-body tr.board-row:nth-child({ROW + 1}) td.tyre-cell .tyre-img"
PROFILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'build', 'edge-cdp-profile-tyre')


async def send(ws, mid, method, **params):
    await ws.send(json.dumps({"id": mid, "method": method, "params": params}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == mid:
            return msg


async def evaluate(ws, mid, expr):
    r = await send(ws, mid, "Runtime.evaluate",
                   expression=expr, returnByValue=True, awaitPromise=True)
    if "exceptionDetails" in r.get("result", {}):
        raise RuntimeError(r["result"]["exceptionDetails"])
    return r["result"].get("result", {}).get("value")


async def click(ws, mid, x, y):
    await send(ws, mid, "Input.dispatchMouseEvent",
               type="mousePressed", x=x, y=y, button="left", clickCount=1)
    await send(ws, mid + 1, "Input.dispatchMouseEvent",
               type="mouseReleased", x=x, y=y, button="left", clickCount=1)
    return mid + 2


async def main():
    assert EDGE, "Edge not found"
    os.makedirs(PROFILE, exist_ok=True)
    proc = subprocess.Popen(
        [EDGE, f"--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
         f"--user-data-dir={PROFILE}",
         "--window-size=1400,900", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        target = None
        for _ in range(50):
            try:
                data = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=2))
                for t in data:
                    if t.get("type") == "page":
                        target = t
                        break
                if target:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        assert target, "no page target"
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            mid = 1
            await send(ws, mid, "Page.enable"); mid += 1
            await send(ws, mid, "Runtime.enable"); mid += 1
            await send(ws, mid, "Page.addScriptToEvaluateOnNewDocument",
                       source=("window.__errs=[];window.addEventListener('error',function(e){"
                               "window.__errs.push(e.message+' @ '+(e.filename||'')+':'+(e.lineno||''));});")); mid += 1
            await send(ws, mid, "Page.navigate", url=URL); mid += 1
            for _ in range(60):
                state = await evaluate(ws, mid, "document.readyState"); mid += 1
                if state == "complete":
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(1.5)

            diag = await evaluate(ws, mid, f"""(() => {{
                var el = document.querySelector('{TYRE_SEL}');
                if (!el) return {{found: false}};
                el.scrollIntoView({{block: 'center'}});
                var r = el.getBoundingClientRect();
                return {{found: true, x: r.left + r.width/2, y: r.top + r.height/2,
                        tip: el.getAttribute('data-tip')}};
            }})()"""); mid += 1
            if not diag.get("found"):
                dbg = await evaluate(ws, mid, """(() => {
                    var cells = document.querySelectorAll('#lb-board-body td.tyre-cell');
                    var rows = document.querySelectorAll('#lb-board-body tr').length;
                    return {ready: document.readyState, url: location.href,
                            rows: rows, tyreCells: cells.length,
                            firstCell: cells.length ? cells[0].innerHTML.slice(0, 160) : null,
                            errs: window.__errs || []};
                })()"""); mid += 1
                print("NO TYRE IMAGES — diag:", dbg)
                return
            print("tyre at", {k: diag[k] for k in ("x", "y", "tip")})
            mid = await click(ws, mid, diag["x"], diag["y"])
            await asyncio.sleep(0.6)

            state = await evaluate(ws, mid, f"""(() => {{
                var p = document.querySelector('.dmg-popup');
                if (!p) return {{popup: false}};
                var cond = p.querySelector('.dmg-cond');
                var row = document.querySelector('{TYRE_SEL}').closest('tr');
                var chip = row ? row.querySelector('.src-cell .chip') : null;
                return {{popup: true, show: p.classList.contains('show'),
                        title: (p.querySelector('.dmg-title') || {{}}).textContent || '',
                        car: !!p.querySelector('.dmg-car'),
                        nodata: !!(p.querySelector('.dmg-nodata')),
                        cond: cond ? cond.textContent : null,
                        row_source: chip ? chip.textContent : '(none)'}};
            }})()"""); mid += 1
            print("after first click:", state)

            mid = await click(ws, mid, diag["x"], diag["y"])
            await asyncio.sleep(0.6)
            closed = await evaluate(ws, mid, """(() => {
                var p = document.querySelector('.dmg-popup');
                return p ? !p.classList.contains('show') : true;
            })()"""); mid += 1
            print("after second click (closed):", closed)
    finally:
        # Kill our own process tree only (headless Edge spawns children); a
        # random debug port per run means a leftover instance can't shadow us.
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            proc.terminate()


asyncio.run(main())
