"""Trusted-click test for the leaderboard car select via CDP.

Loads the live leaderboard (no params), opens the car menu with real mouse
events, clicks a car option, then reports the URL the page navigated to and
the label the reloaded page shows — i.e. exactly what form.submit() sent and
whether the selection survives the reload. Needs local Edge + system Python
with `websockets`.

Usage: check_live_car_select_click.py [URL] [CAR_VID]
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request

import websockets

import os

EDGE = next((p for p in (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
             if os.path.exists(p)), None)
PORT = 9333
URL = sys.argv[1] if len(sys.argv) > 1 else "https://byagersi.com/rally/leaderboard"
CAR_VID = sys.argv[2] if len(sys.argv) > 2 else "572"


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
    mid += 2
    return mid


async def main():
    proc = subprocess.Popen(
        [EDGE, f"--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}",
         "--user-data-dir=C:/Users/Agersi/AppData/Local/Temp/edge-cdp-dr3",
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
            await send(ws, mid, "Page.navigate", url=URL); mid += 1
            for _ in range(60):
                state = await evaluate(ws, mid, "document.readyState"); mid += 1
                if state == "complete":
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(1.0)

            await send(ws, mid, "Page.bringToFront"); mid += 1
            diag = await evaluate(ws, mid, """(() => {
                var b = document.querySelector('.cselect:has(select[name=car]) .cselect-btn');
                var r = b.getBoundingClientRect();
                var x = r.left + r.width/2, y = r.top + r.height/2;
                var el = document.elementFromPoint(x, y);
                window.__mdCount = 0;
                b.addEventListener('mousedown', function () { window.__mdCount += 1; });
                return {x: x, y: y, at: el ? el.className + '<' + el.tagName + '>' : 'null',
                        dpr: window.devicePixelRatio, inner: [window.innerWidth, window.innerHeight]};
            })()"""); mid += 1
            print("diag:", diag)
            rect = {"x": diag["x"], "y": diag["y"]}
            mid = await click(ws, mid, rect["x"], rect["y"])
            await asyncio.sleep(0.5)
            opened = await evaluate(ws, mid, """(() => {
                var w = document.querySelector('.cselect:has(select[name=car])');
                return {open: w.classList.contains('open'), md: window.__mdCount};
            })()""")
            print("after button click:", opened); mid += 1

            await evaluate(ws, mid, f"""(() => {{
                var li = document.querySelector('.cselect:has(select[name=car]) .cselect-item[data-value="{CAR_VID}"]');
                li.scrollIntoView({{block: 'center'}});
            }})()"""); mid += 1
            rect = await evaluate(ws, mid, f"""(() => {{
                var li = document.querySelector('.cselect:has(select[name=car]) .cselect-item[data-value="{CAR_VID}"]');
                var r = li.getBoundingClientRect();
                return {{x: r.left + r.width/2, y: r.top + r.height/2}};
            }})()"""); mid += 1
            print("clicking option at", rect)
            mid = await click(ws, mid, rect["x"], rect["y"])
            await asyncio.sleep(2.5)

            href = await evaluate(ws, mid, "location.href"); mid += 1
            print("FINAL URL:", href)
            label = await evaluate(ws, mid, """(() => {
                var w = document.querySelector('.cselect:has(select[name=car])');
                return w ? w.querySelector('.cselect-label').textContent : 'NO WIDGET';
            })()"""); mid += 1
            print("car select label now:", label)
    finally:
        proc.terminate()


asyncio.run(main())
