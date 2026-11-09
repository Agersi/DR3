"""Verify the Main-tab telemetry status indicator in dr3_app.py:

- tel_status_text() maps every state to the right text/color key
  (stopped/hidden, disabled in config, port busy, waiting for data,
  connected, stale-after-data, and the game-aware hints: UDP disabled -> warn,
  game already on DR3's port -> muted, game on a different port -> warn);
- the real GUI boots with the new status-card row, and while the server is
  stopped the Main tab still fits the fixed window (no scrollbar) and the
  telemetry row exists but is hidden.

Exits non-zero on any failure.
"""
import importlib.util
import sys
import time
from pathlib import Path


def _load_dr3_app():
    path = Path(__file__).resolve().parent.parent / "dr3_app.py"
    sys.path.insert(0, str(path.parent))  # dr2server/ package next to dr3_app.py
    spec = importlib.util.spec_from_file_location("dr3_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeListener:
    def __init__(self, port=20999, packets_seen=0, last_packet_wall=0.0):
        self.port = port
        self.packets_seen = packets_seen
        self.last_packet_wall = last_packet_wall


def check_states(mod) -> int:
    failures = 0

    def check(name, cond, detail=""):
        nonlocal failures
        if cond:
            print(f"OK   {name}")
        else:
            failures += 1
            print(f"FAIL {name} {detail}")

    t = mod.tel_status_text
    now = time.time()

    check("stopped -> hidden (None)",
          t(False, True, {"port": None}, FakeListener(packets_seen=3, last_packet_wall=now)) is None)

    st = t(True, False, {"port": 20999}, FakeListener(packets_seen=3, last_packet_wall=now))
    check("disabled in config.json",
          st == ("Telemetry disabled in config.json", "muted"), repr(st))

    st = t(True, True, {"port": 20777}, None)
    check("bind failed -> port busy (red + port shown)",
          st is not None and st[1] == "bad" and ":20777" in st[0] and "\u2717" in st[0], repr(st))

    st = t(True, True, {"port": None}, None)
    check("bind failed, port unknown", st is not None and st[1] == "bad", repr(st))

    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=0))
    check("listening, no data yet",
          st == ("Telemetry waiting for data on :20999", "muted"), repr(st))

    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=5, last_packet_wall=now))
    check("connected (fresh packets)",
          st is not None and st[1] == "good" and "\u2713" in st[0] and ":20999" in st[0], repr(st))

    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=5, last_packet_wall=now - 30))
    check("stale after having seen data",
          st is not None and st[1] == "muted" and "no recent data" in st[0], repr(st))

    # Game-aware hints (server up, listening, never seen a packet).
    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=0),
           game={"present": True, "enabled": False, "port": None})
    check("game UDP disabled -> warn (restart the game)",
          st is not None and st[1] == "warn" and "restart the game" in st[0], repr(st))

    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=0),
           game={"present": True, "enabled": True, "port": 20999})
    check("game already on DR3 port -> muted (start a stage)",
          st is not None and st[1] == "muted" and "start a stage" in st[0], repr(st))

    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=0),
           game={"present": True, "enabled": True, "port": 20777})
    check("game on a different port -> warn (forward in SimHub)",
          st is not None and st[1] == "warn" and ":20777" in st[0]
          and "SimHub" in st[0], repr(st))

    st = t(True, True, {"port": 20999}, FakeListener(packets_seen=0),
           game={"present": True, "enabled": True, "port": None})
    check("game enabled but no parseable port -> muted",
          st is not None and st[1] == "muted" and "start a stage" in st[0], repr(st))

    return failures


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def check_gui(mod) -> int:
    import tkinter as tk

    captured = {}

    def fake_mainloop(self, n=0):
        captured["root"] = self

    orig_mainloop = tk.Tk.mainloop
    tk.Tk.mainloop = fake_mainloop
    try:
        mod.run_gui()
    finally:
        tk.Tk.mainloop = orig_mainloop

    root = captured.get("root")
    if root is None:
        print("FAIL run_gui() did not reach mainloop")
        return 1

    failures = 0
    root.update_idletasks()
    root.update()

    notebook = next((w for w in root.winfo_children() if w.winfo_class() == "TNotebook"), None)
    if notebook is None:
        print("FAIL no TNotebook found")
        return 1

    tabs = notebook.winfo_children()
    names = ["Main", "Overlay", "Logs"]
    for name, outer in zip(names, tabs):
        # Only the selected tab is mapped — select each one to measure it.
        notebook.select(outer)
        root.update_idletasks()
        root.update()
        sb = next((c for c in outer.winfo_children() if c.winfo_class() == "TScrollbar"), None)
        canvas = next((c for c in outer.winfo_children() if c.winfo_class() == "Canvas"), None)
        content_h = 0
        visible_h = 0
        if canvas is not None:
            bbox = canvas.bbox("all")
            content_h = bbox[3] if bbox else 0
            visible_h = canvas.winfo_height()
        mapped = sb is not None and sb.winfo_ismapped()
        fits = content_h <= visible_h + 2
        print(f"INFO {name}: content={content_h}px visible={visible_h}px scrollbar={'shown' if mapped else 'hidden'}")
        if name in ("Main", "Logs") and (mapped or not fits):
            failures += 1
            print(f"FAIL {name} tab overflows the window (scrollbar would appear)")
    notebook.select(tabs[0])
    root.update()

    main_outer = tabs[0]
    tel_labels = []
    for f in _walk(main_outer):
        if f.winfo_class() != "Frame":
            continue
        kids = f.winfo_children()
        if len(kids) != 2:
            continue
        kinds = {c.winfo_class() for c in kids}
        if kinds == {"Frame", "Label"} and str(f.cget("highlightthickness")) == "1":
            tel_labels.append(next(c for c in kids if c.winfo_class() == "Label"))

    # While the server runs the row is packed — confirm Main still fits then.
    if len(tel_labels) == 1:
        tel = tel_labels[0]
        tel.configure(text="Telemetry listening on :20999 \u2014 no recent data")
        tel.pack(fill="x", padx=15, pady=(0, 8))
        root.update_idletasks()
        root.update()
        canvas = next(c for c in main_outer.winfo_children() if c.winfo_class() == "Canvas")
        bbox = canvas.bbox("all")
        running_h = bbox[3] if bbox else 0
        visible_h = canvas.winfo_height()
        print(f"INFO Main with telemetry row: content={running_h}px visible={visible_h}px")
        tel.pack_forget()
        root.update()
        if running_h > visible_h + 2:
            failures += 1
            print("FAIL Main tab overflows the window with the telemetry row shown")

    if len(tel_labels) == 1 and not tel_labels[0].winfo_ismapped():
        print("OK   telemetry row exists in the status card, hidden while stopped")
    else:
        failures += 1
        print(f"FAIL telemetry row lookup: {len(tel_labels)} candidate(s)")

    root.destroy()
    return failures


def main() -> int:
    mod = _load_dr3_app()
    failures = check_states(mod)
    failures += check_gui(mod)
    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
