"""Verify the close flow + window geometry in dr3_app.py:

- the window is the fixed 440x610 size (tall enough that the update banner
  never forces a scrollbar on Main/Logs);
- with the update bar shown, Main and Logs still fit (no scrollbar) while
  Overlay keeps its scrollbar; Main also fits with the telemetry row packed;
- closing with leftover hosts (server stopped) shows a modal dialog with a
  labeled proceed button ("Close anyway") + Cancel — Cancel keeps the app
  open, "Close anyway" exits;
- closing with nothing to stop (no hosts, server down) exits without any
  dialog.

Exits non-zero on any failure.
"""
import importlib.util
import sys
from pathlib import Path


def _load_dr3_app():
    path = Path(__file__).resolve().parent.parent / "dr3_app.py"
    sys.path.insert(0, str(path.parent))  # dr2server/ package next to dr3_app.py
    spec = importlib.util.spec_from_file_location("dr3_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hermetic_config(mod):
    """Serve a config that never auto-starts the server (test hermeticity)."""
    orig_load_config = mod.load_config

    def quiet_load_config():
        cfg = orig_load_config()
        cfg["auto_start_server"] = False
        return cfg

    return orig_load_config, quiet_load_config


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _find_update_bar(root):
    for child in root.winfo_children():
        if child.winfo_class() != "Frame":
            continue
        kids = child.winfo_children()
        if len(kids) == 1 and kids[0].winfo_class() == "Label":
            return child, kids[0]
    return None, None


def _find_telemetry_row(root):
    for f in _walk(root):
        if f.winfo_class() != "Frame":
            continue
        kids = f.winfo_children()
        if len(kids) != 2:
            continue
        kinds = {c.winfo_class() for c in kids}
        if kinds == {"Frame", "Label"} and str(f.cget("highlightthickness")) == "1":
            return next(c for c in kids if c.winfo_class() == "Label")
    return None


def check_geometry_and_banner_fit(mod) -> int:
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

    geom = root.winfo_geometry()
    size = geom.split("+")[0]
    if size != "440x610":
        failures += 1
        print(f"FAIL window geometry is {size}, expected 440x610")
    else:
        print("OK   window is fixed 440x610")

    bar, label = _find_update_bar(root)
    if bar is None or label is None:
        print("FAIL update bar (Frame>Label) not found among root's children")
        return failures + 1
    # Simulate the banner being shown (what _set_update_bar does on "ready").
    label.configure(text="Update v9.9.9 ready — installs when DR3 closes")
    if not bar.winfo_ismapped():
        notebook = next(w for w in root.winfo_children()
                        if w.winfo_class() == "TNotebook")
        bar.pack(fill="x", padx=20, pady=(5, 0), before=notebook)
    root.update_idletasks()
    root.update()

    notebook = next(w for w in root.winfo_children()
                    if w.winfo_class() == "TNotebook")
    tabs = notebook.winfo_children()
    names = ["Main", "Overlay", "Logs"]
    for name, outer in zip(names, tabs):
        notebook.select(outer)
        root.update_idletasks()
        root.update()
        sb = next((c for c in outer.winfo_children()
                   if c.winfo_class() == "TScrollbar"), None)
        canvas = next((c for c in outer.winfo_children()
                       if c.winfo_class() == "Canvas"), None)
        bbox = canvas.bbox("all") if canvas is not None else None
        content_h = bbox[3] if bbox else 0
        visible_h = canvas.winfo_height() if canvas is not None else 0
        mapped = sb is not None and sb.winfo_ismapped()
        print(f"INFO {name} (banner shown): content={content_h}px "
              f"visible={visible_h}px scrollbar={'shown' if mapped else 'hidden'}")
        if name in ("Main", "Logs") and (mapped or content_h > visible_h + 2):
            failures += 1
            print(f"FAIL {name} tab overflows the window with the banner shown")

    # Main with the telemetry row packed too (server-running state).
    notebook.select(tabs[0])
    tel = _find_telemetry_row(root)
    if tel is not None:
        tel.configure(text="Telemetry listening on :20999 — no recent data")
        tel.pack(fill="x", padx=15, pady=(0, 8))
        root.update_idletasks()
        root.update()
        canvas = next(c for c in tabs[0].winfo_children()
                      if c.winfo_class() == "Canvas")
        bbox = canvas.bbox("all")
        running_h = bbox[3] if bbox else 0
        visible_h = canvas.winfo_height()
        tel.pack_forget()
        root.update()
        print(f"INFO Main (banner + telemetry row): content={running_h}px "
              f"visible={visible_h}px")
        if running_h > visible_h + 2:
            failures += 1
            print("FAIL Main tab overflows with banner + telemetry row shown")

    # Close through the real WM_DELETE_WINDOW path (hosts off → no dialog),
    # so the app's own teardown runs and no Tcl callback outlives it.
    saved_hosts = mod.hosts_configured
    mod.hosts_configured = lambda: False
    try:
        root.tk.call(root.protocol("WM_DELETE_WINDOW"))
    finally:
        mod.hosts_configured = saved_hosts
    try:
        alive = bool(root.winfo_exists())
    except tk.TclError:
        alive = False  # interpreter already destroyed = closed
    if alive:
        failures += 1
        print("FAIL window still exists after clean close")
    return failures


def _run_close_scenario(mod, hosts_configured, expect_dialog: bool) -> int:
    """Boot the GUI, trigger WM_DELETE_WINDOW once (or twice when a dialog is
    expected), and check the outcome. Returns failure count."""
    import tkinter as tk

    captured = {}
    created_toplevels = []
    orig_toplevel = tk.Toplevel

    class TrackedToplevel(orig_toplevel):
        def __init__(self, *a, **kw):
            created_toplevels.append(self)
            super().__init__(*a, **kw)

    results = {"dialog_texts": None, "question": None, "cancelled_alive": False,
               "closed": False}

    def fake_mainloop(self, n=0):
        captured["root"] = self
        orig_mainloop(self)

    def button_texts(dlg):
        return sorted(str(b.cget("text")) for b in _walk(dlg)
                      if b.winfo_class() == "Button")

    def label_text(dlg):
        lbl = next((w for w in _walk(dlg) if w.winfo_class() == "Label"), None)
        return str(lbl.cget("text")) if lbl is not None else ""

    def trigger_close():
        self_cmd = captured["root"].protocol("WM_DELETE_WINDOW")
        captured["root"].tk.call(self_cmd)

    import tempfile

    orig_mainloop = tk.Tk.mainloop
    orig_hosts = mod.hosts_configured
    orig_updates_dir = mod.UPDATES_DIR
    tmp_updates = Path(tempfile.mkdtemp(prefix="dr3_close_check_")) / "updates"
    tmp_updates.mkdir()
    tk.Toplevel = TrackedToplevel
    mod.hosts_configured = hosts_configured
    mod.UPDATES_DIR = tmp_updates  # never hand a real staged zip to a child
    try:
        def fake_mainloop2(self, n=0):
            captured["root"] = self

            def first_attempt():
                if expect_dialog:
                    # Fires inside wait_window's nested loop: verify the
                    # dialog and click Cancel.
                    def cancel_click():
                        dlg = created_toplevels[-1]
                        results["dialog_texts"] = button_texts(dlg)
                        results["question"] = label_text(dlg)
                        for b in _walk(dlg):
                            if b.winfo_class() == "Button" \
                                    and str(b.cget("text")) == "Cancel":
                                b.invoke()
                                break
                    self.after(400, cancel_click)
                trigger_close()

            def second_attempt():
                # Only reached when the first attempt was cancelled.
                def proceed_click():
                    dlg = created_toplevels[-1]
                    for b in _walk(dlg):
                        if b.winfo_class() == "Button" \
                                and str(b.cget("text")) != "Cancel":
                            b.invoke()  # the labeled proceed button
                            break
                    results["closed"] = True
                self.after(400, proceed_click)
                trigger_close()

            if expect_dialog:
                # Before the status ticker's first 2 s fire, so the teardown
                # has no pending self-rescheduled callback (Tkinter prints a
                # harmless "invalid command name" for those).
                self.after(1500, second_attempt)
                self.after(300, first_attempt)
            else:
                self.after(300, first_attempt)
            orig_mainloop(self)

        tk.Tk.mainloop = fake_mainloop2
        mod.run_gui()
    finally:
        tk.Tk.mainloop = orig_mainloop
        tk.Toplevel = orig_toplevel
        mod.hosts_configured = orig_hosts

    root = captured.get("root")
    if root is None:
        print("FAIL run_gui() did not reach mainloop (close scenario)")
        return 1

    failures = 0
    if expect_dialog:
        if len(created_toplevels) != 2:
            failures += 1
            print(f"FAIL expected 2 dialog windows (cancel + proceed), "
                  f"saw {len(created_toplevels)}")
        texts = results.get("dialog_texts") or []
        if "Cancel" not in texts or len(texts) != 2:
            failures += 1
            print(f"FAIL dialog buttons are {texts}, expected Cancel + one more")
        elif not any(t for t in texts if t != "Cancel"):
            failures += 1
            print("FAIL proceed button has no label")
        else:
            print(f"OK   close dialog buttons: {texts}")
        question = results.get("question") or ""
        if "hosts" not in question.lower():
            failures += 1
            print(f"FAIL dialog question does not mention hosts: {question!r}")
        else:
            print("OK   dialog question explains the hosts redirect")
        if not results["closed"]:
            failures += 1
            print("FAIL proceed button did not close the app")
        else:
            print("OK   proceed button closes the app after cancel kept it open")
    else:
        if created_toplevels:
            failures += 1
            print(f"FAIL clean close opened a dialog ({len(created_toplevels)} window(s))")
        else:
            print("OK   clean close (nothing to stop) exits without a dialog")

    return failures


def main() -> int:
    mod = _load_dr3_app()
    orig_load_config, quiet_load_config = _hermetic_config(mod)
    failures = 0
    try:
        mod.load_config = quiet_load_config
        failures += check_geometry_and_banner_fit(mod)
        # Leftover hosts, server stopped -> dialog with Cancel + proceed.
        failures += _run_close_scenario(
            mod, lambda: True, expect_dialog=True)
        # Nothing to stop -> straight exit, no dialog.
        failures += _run_close_scenario(
            mod, lambda: False, expect_dialog=False)
    finally:
        mod.load_config = orig_load_config

    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
