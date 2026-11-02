"""Verify the fully-automated auto-updater in dr3_app.py (backlog #51):

- _version_tuple parses version tags (digits only, None for non-numeric);
- _staged_update picks the newest staged zip that is NEWER than the running
  version and ignores older/non-numeric names;
- _validate_update_zip accepts only real archives carrying the onedir layout
  (top-level DR3/ with DR3.exe inside);
- _download_update's failure path leaves no partial file behind;
- _apply_update performs the full swap in temp dirs: new contents land in the
  install dir, the previous one is kept as DR3.bak-<ver>, the staged zip and
  the staging folder are removed; a vanished install dir aborts cleanly;
- the `--apply-update` CLI branch exits before any Tk/logging machinery
  (source mode: not an installed bundle -> rc 0, note in update.log);
- the real GUI boots with the gold bar hidden, and end-to-end against a local
  HTTP server: check -> auto-download -> staged zip on disk -> "ready" banner.

Exits non-zero on any failure.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _load_dr3_app():
    path = Path(__file__).resolve().parent.parent / "dr3_app.py"
    sys.path.insert(0, str(path.parent))  # dr2server/ package next to dr3_app.py
    spec = importlib.util.spec_from_file_location("dr3_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _failures():
    return {"n": 0}


def check_versions(mod, f):
    t = mod._version_tuple
    cases = [
        ("0.1.48", (0, 1, 48)),
        ("v1.2.3", (1, 2, 3)),
        ("1.2.x.3", (1, 2, 3)),
        ("dev", None),
        ("", None),
    ]
    for tag, want in cases:
        got = t(tag)
        if got != want:
            f["n"] += 1
            print(f"FAIL _version_tuple({tag!r}) = {got!r}, want {want!r}")
        else:
            print(f"OK   _version_tuple({tag!r})")


def check_staged(mod, f):
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    orig_dir, orig_ver = mod.UPDATES_DIR, mod.VERSION
    try:
        (tmp / "updates").mkdir()
        mod.UPDATES_DIR = tmp / "updates"

        if mod._staged_update() is not None:
            f["n"] += 1
            print("FAIL empty updates dir should give None")
        else:
            print("OK   no staged zips -> None")

        # Older than the running version and non-numeric names are ignored.
        (mod.UPDATES_DIR / "DR3-windows-0.0.1.zip").write_bytes(b"zip")
        (mod.UPDATES_DIR / "DR3-windows-dev.zip").write_bytes(b"zip")
        if mod._staged_update() is not None:
            f["n"] += 1
            print("FAIL older/dev staged zips must be ignored")
        else:
            print("OK   older + non-numeric staged zips ignored")

        # Newest of several newer versions wins.
        (mod.UPDATES_DIR / "DR3-windows-0.1.99.zip").write_bytes(b"zip")
        (mod.UPDATES_DIR / "DR3-windows-0.2.0.zip").write_bytes(b"zip")
        best = mod._staged_update()
        if not (best and best[0].name == "DR3-windows-0.2.0.zip"
                and best[1] == (0, 2, 0)):
            f["n"] += 1
            print(f"FAIL expected 0.2.0 to win, got {best!r}")
        else:
            print("OK   newest staged zip wins (0.2.0 over 0.1.99)")

        # A staged zip older than the running version is never installable.
        mod.VERSION = "0.3.0"
        if mod._staged_update() is not None:
            f["n"] += 1
            print("FAIL staged zips <= running version must be ignored")
        else:
            print("OK   staged zips <= running version ignored")
    finally:
        mod.UPDATES_DIR, mod.VERSION = orig_dir, orig_ver
        shutil.rmtree(tmp, ignore_errors=True)


def _make_update_zip(path: Path, exe_content: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DR3/", b"")
        zf.writestr("DR3/DR3.exe", exe_content)
        zf.writestr("DR3/_internal/lib.dll", b"dll")


def check_validate(mod, f):
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    try:
        good = tmp / "good.zip"
        _make_update_zip(good, b"exe")
        if not mod._validate_update_zip(good):
            f["n"] += 1
            print("FAIL valid onedir zip rejected")
        else:
            print("OK   valid onedir zip accepted")

        bad = tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("other/thing.txt", b"x")
        if mod._validate_update_zip(bad):
            f["n"] += 1
            print("FAIL zip without DR3/DR3.exe accepted")
        else:
            print("OK   wrong-layout zip rejected")

        notzip = tmp / "no.zip"
        notzip.write_bytes(b"this is not a zip")
        if mod._validate_update_zip(notzip):
            f["n"] += 1
            print("FAIL non-zip accepted")
        else:
            print("OK   non-zip rejected")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_download_failure(mod, f):
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    orig_dir = mod.UPDATES_DIR
    try:
        (tmp / "updates").mkdir()
        mod.UPDATES_DIR = tmp / "updates"
        calls = []
        # Port 9 (discard) on loopback refuses connections fast.
        result = mod._download_update(
            "http://127.0.0.1:9/DR3-windows.zip", "9.9.9",
            lambda d, t: calls.append((d, t)))
        leftovers = list(tmp.glob("updates/*.part")) + \
            list(tmp.glob("updates/DR3-windows-*.zip"))
        if result is not None:
            f["n"] += 1
            print(f"FAIL unreachable URL should give None, got {result!r}")
        elif leftovers:
            f["n"] += 1
            print(f"FAIL partial files left behind: {leftovers}")
        else:
            print("OK   failed download -> None, no partial file")
    finally:
        mod.UPDATES_DIR = orig_dir
        shutil.rmtree(tmp, ignore_errors=True)


def check_apply(mod, f):
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    install = tmp / "DR3"
    (install / "_internal").mkdir(parents=True)
    (install / "DR3.exe").write_bytes(b"old exe")
    (install / "_internal" / "lib.dll").write_bytes(b"old dll")

    staged = tmp / "staged"
    staged.mkdir()
    zip_path = staged / f"DR3-windows-9.9.9.zip"
    _make_update_zip(zip_path, b"new exe")

    rc = mod._apply_update(zip_path, install, parent_pid=0)  # 0 = already gone
    if rc != 0:
        f["n"] += 1
        print(f"FAIL _apply_update rc={rc}")
    else:
        print("OK   _apply_update rc=0")
    if (install / "DR3.exe").read_bytes() != b"new exe":
        f["n"] += 1
        print("FAIL install dir does not carry the new contents")
    elif not (install / "_internal" / "lib.dll").is_file():
        f["n"] += 1
        print("FAIL new bundle's _internal missing after swap")
    else:
        print("OK   install dir carries the new bundle")

    bak = tmp / f"DR3.bak-{mod.VERSION}"
    if not (bak / "DR3.exe").is_file() or (bak / "DR3.exe").read_bytes() != b"old exe":
        f["n"] += 1
        print(f"FAIL previous install not kept as {bak.name}")
    else:
        print(f"OK   previous install kept as DR3.bak-{mod.VERSION}")

    if zip_path.exists():
        f["n"] += 1
        print("FAIL staged zip not removed after success")
    if (tmp / "DR3.update-9.9.9").exists():
        f["n"] += 1
        print("FAIL staging folder left behind")
    if not zip_path.exists() and not (tmp / "DR3.update-9.9.9").exists():
        print("OK   staged zip + staging folder cleaned up")

    # A vanished install dir (uninstalled meanwhile) aborts without touching
    # anything — rc 4, and no backup created.
    rc = mod._apply_update(zip_path, tmp / "gone", parent_pid=0)
    if rc != 4:
        f["n"] += 1
        print(f"FAIL vanished install dir should give rc=4, got {rc}")
    else:
        print("OK   vanished install dir -> rc=4")

    shutil.rmtree(tmp, ignore_errors=True)


def check_cli_source_mode(mod, f):
    dr3_app = Path(__file__).resolve().parent.parent / "dr3_app.py"
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    try:
        # Point the child's DR3_DIR at a temp dir so update.log stays local.
        env = dict(os.environ)
        if sys.platform == "win32":
            env["APPDATA"] = str(tmp / "appdata")
        else:
            env["XDG_CONFIG_HOME"] = str(tmp / "xdg")
        proc = subprocess.run(
            [sys.executable, str(dr3_app), "--apply-update",
             str(tmp / "missing.zip"), "0"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        log = tmp / ("appdata" if sys.platform == "win32" else "xdg")
        logs = list(log.rglob("update.log"))
        if proc.returncode != 0:
            f["n"] += 1
            print(f"FAIL --apply-update in source mode rc={proc.returncode} "
                  f"stderr={proc.stderr[:200]!r}")
        elif not logs or "not an installed DR3.exe bundle" not in logs[0].read_text():
            f["n"] += 1
            print(f"FAIL update.log missing the source-mode note: {logs}")
        else:
            print("OK   --apply-update CLI branch runs pre-Tk, rc=0 in source mode")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_gui(mod, f):
    import tkinter as tk

    # Local server: /version.json + a real update zip at the download URL.
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    docroot = tmp / "www"
    docroot.mkdir()
    (docroot / "DR3-windows.zip").write_bytes(b"")
    _make_update_zip(docroot / "DR3-windows.zip", b"gui exe")

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(docroot), **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/version.json":
                body = json.dumps({
                    "ok": True,
                    "version": "9.9.9",
                    "download_url": f"http://127.0.0.1:{self.server.server_address[1]}/DR3-windows.zip",
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    updates = tmp / "updates"
    updates.mkdir()
    install_stub = tmp / "DR3"  # source mode: _install_dir is patched to this
    orig = (mod.VERSION_CHECK_URL, mod.UPDATES_DIR, mod._install_dir)
    captured = {}

    def fake_mainloop(self, n=0):
        # Run the REAL mainloop (only it sets Tcl's in-main-loop flag, which
        # is what makes worker-thread root.after() calls succeed); a 50ms tick
        # captures the final banner state and destroys the window — the same
        # way on_close ends the session — once the flow reaches "ready" or the
        # timeout hits. (tkinter has no break_mainloop before Python 3.14.)
        captured["root"] = self
        label = None
        for child in self.winfo_children():
            if child.winfo_class() != "Frame":
                continue
            kids = child.winfo_children()
            if len(kids) == 1 and kids[0].winfo_class() == "Label":
                label = kids[0]
        captured["label"] = label
        captured["bar_mapped_at_boot"] = (
            label.winfo_ismapped() if label is not None else None)
        deadline = time.time() + 15

        def tick():
            text = str(label.cget("text")) if label is not None else ""
            if "Update v9.9.9 ready" in text or time.time() > deadline:
                captured["final_text"] = text
                captured["final_mapped"] = (
                    label.winfo_ismapped() if label is not None else False)
                self.destroy()
                return
            self.after(50, tick)

        self.after(100, tick)
        orig_mainloop(self)

    orig_mainloop = tk.Tk.mainloop
    try:
        mod.VERSION_CHECK_URL = f"http://127.0.0.1:{port}/version.json"
        mod.UPDATES_DIR = updates
        mod._install_dir = lambda: install_stub
        tk.Tk.mainloop = fake_mainloop
        mod.run_gui()
    finally:
        tk.Tk.mainloop = orig_mainloop
        httpd.shutdown()

    root = captured.get("root")
    if root is None:
        f["n"] += 1
        print("FAIL run_gui() did not reach mainloop")
        return

    if captured.get("label") is None:
        f["n"] += 1
        print("FAIL update bar (Frame>Label) not found among root's children")
        return
    if captured.get("bar_mapped_at_boot"):
        f["n"] += 1
        print("FAIL update bar visible before any check completed")
    else:
        print("OK   update bar exists, hidden at boot")

    text = str(captured.get("final_text", ""))
    if "Update v9.9.9 ready — installs when DR3 closes" not in text \
            or not captured.get("final_mapped"):
        f["n"] += 1
        print(f"FAIL banner did not reach ready state, got {text!r}")
    else:
        print("OK   check -> auto-download -> 'ready' banner (end-to-end)")

    staged = list(updates.glob("DR3-windows-*.zip"))
    if len(staged) != 1 or not mod._validate_update_zip(staged[0]):
        f["n"] += 1
        print(f"FAIL staged download missing/invalid: {staged}")
    else:
        print(f"OK   staged zip on disk: {staged[0].name} (valid)")

    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    mod = _load_dr3_app()
    f = _failures()
    check_versions(mod, f)
    check_staged(mod, f)
    check_validate(mod, f)
    check_download_failure(mod, f)
    check_apply(mod, f)
    check_cli_source_mode(mod, f)
    check_gui(mod, f)
    if f["n"]:
        print(f"\n{f['n']} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
