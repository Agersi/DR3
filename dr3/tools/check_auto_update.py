"""Verify the fully-automated auto-updater in dr3_app.py (backlog #51):

- _version_tuple parses version tags (digits only, None for non-numeric);
- _staged_update picks the newest staged zip that is NEWER than the running
  version and ignores older/non-numeric names;
- _validate_update_zip accepts only real archives carrying the onedir layout
  (top-level DR3/ with DR3.exe inside) AND an INSTALL_MANIFEST.json;
- _update_url_allowed admits only https URLs on our own domain (+ loopback);
- _verify_update_signature accepts the Ed25519 release signature and rejects
  tampered payloads/signatures (the download is fetched as <url>.sig);
- _install_dir only accepts a bundle named DR3/ with a matching manifest;
- _download_update's failure path leaves no partial file behind;
- _apply_update performs the full swap in temp dirs: new contents land in the
  install dir, the previous one is kept as DR3.bak-<ver>, the staged zip and
  the staging folder are removed; a vanished install dir aborts cleanly;
- after a successful swap a `relaunch` child starts the new DR3.exe (like a
  double-click would: CWD = install dir, detached); if that Popen fails it
  falls back to opening the install folder in Explorer; a non-relaunch child
  (stale zip from an earlier session) keeps the legacy explorer behavior; a
  failed install must trigger neither;
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
from typing import Optional


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


def _make_update_zip(path: Path, exe_content: bytes, version: Optional[str] = None) -> None:
    """Build a minimal onedir-layout zip WITH a valid INSTALL_MANIFEST.json.

    ``version`` defaults to the tag implied by the file name
    (DR3-windows-<ver>.zip) so _apply_update's manifest-vs-filename cross
    check passes."""
    if version is None:
        version = path.name[len("DR3-windows-"):-len(".zip")]
    manifest = {
        "version": version,
        "files": ["DR3.exe", "_internal/lib.dll", "INSTALL_MANIFEST.json"],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DR3/", b"")
        zf.writestr("DR3/DR3.exe", exe_content)
        zf.writestr("DR3/_internal/lib.dll", b"dll")
        zf.writestr("DR3/INSTALL_MANIFEST.json", json.dumps(manifest))


def _sign_zip(zip_path: Path) -> bytes:
    """Ed25519-sign the zip with the release private key (tools/keys/)."""
    from cryptography.hazmat.primitives import serialization
    key_path = Path(__file__).resolve().parents[2] / "tools" / "keys" / \
        "dr3_update_ed25519.key"
    if not key_path.is_file():
        raise SystemExit(
            f"{key_path} missing — run tools/generate_update_key.py first")
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    return key.sign(zip_path.read_bytes())


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

        # C2: a layout-correct zip WITHOUT the ownership manifest is not an
        # installable bundle.
        nomani = tmp / "nomani.zip"
        with zipfile.ZipFile(nomani, "w") as zf:
            zf.writestr("DR3/", b"")
            zf.writestr("DR3/DR3.exe", b"exe")
        if mod._validate_update_zip(nomani):
            f["n"] += 1
            print("FAIL zip without INSTALL_MANIFEST.json accepted")
        else:
            print("OK   manifest-less zip rejected")

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

    # C2: a zip whose manifest version disagrees with its file name is a
    # tampered/mislabelled bundle — rc 10, nothing touched.
    mismatch_zip = staged / "DR3-windows-8.8.8.zip"
    _make_update_zip(mismatch_zip, b"new exe", version="7.7.7")
    install_before = (install / "DR3.exe").read_bytes()
    rc = mod._apply_update(mismatch_zip, install, parent_pid=0)
    if rc != 10:
        f["n"] += 1
        print(f"FAIL manifest/filename version mismatch should give rc=10, got {rc}")
    elif (install / "DR3.exe").read_bytes() != install_before:
        f["n"] += 1
        print("FAIL rc=10 path still modified the install dir")
    else:
        print("OK   manifest/filename version mismatch -> rc=10, dir untouched")

    shutil.rmtree(tmp, ignore_errors=True)


def check_cwd_swap(mod, f):
    """The installer child's CWD may be inside install_dir (inherited from a
    double-clicked GUI) — Windows refuses to rename a directory that is any
    process's current working directory. The spawn now starts the child one
    level up and _run_apply_update chdirs out; this proves the mechanism:
    with CWD inside the swap fails, with CWD outside it succeeds."""
    import os as _os

    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    install = tmp / "DR3"
    (install / "_internal").mkdir(parents=True)
    (install / "DR3.exe").write_bytes(b"old exe")
    staged = tmp / "staged"
    staged.mkdir()

    def make_zip(name: str) -> Path:
        p = staged / name
        _make_update_zip(p, b"new exe")
        return p

    old_cwd = Path.cwd()
    try:
        if sys.platform == "win32":
            zip1 = make_zip("DR3-windows-9.9.8.zip")
            _os.chdir(install)  # child inherits CWD = install dir
            rc = mod._apply_update(zip1, install, parent_pid=0)
            if rc == 0:
                f["n"] += 1
                print("FAIL swap with CWD inside install_dir should fail on Windows")
            else:
                print(f"OK   swap with CWD inside install_dir fails (rc={rc}) "
                      f"— avoided by spawn cwd + chdir in _run_apply_update")

        zip2 = make_zip("DR3-windows-9.9.7.zip")
        _os.chdir(tmp)  # the fix: child starts / chdirs one level up
        rc = mod._apply_update(zip2, install, parent_pid=0)
        if rc != 0:
            f["n"] += 1
            print(f"FAIL swap with CWD outside install_dir should succeed, rc={rc}")
        elif (install / "DR3.exe").read_bytes() != b"new exe":
            f["n"] += 1
            print("FAIL swap from outside did not replace the bundle")
        else:
            print("OK   swap succeeds once CWD is outside install_dir")
    finally:
        _os.chdir(old_cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def check_relaunch(mod, f):
    """After a successful swap: a `relaunch` child (this session downloaded the
    zip) starts the new DR3.exe like a double-click would; if that Popen fails
    it falls back to Explorer. A non-relaunch child (stale zip from an earlier
    session) keeps the legacy explorer behavior. A failed install triggers
    neither."""
    import types

    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    install = tmp / "DR3"
    (install / "_internal").mkdir(parents=True)
    (install / "DR3.exe").write_bytes(b"old exe")
    staged = tmp / "staged"
    staged.mkdir()

    calls = []
    real_subprocess = mod.subprocess
    fake_subprocess = types.SimpleNamespace(
        Popen=lambda *a, **k: calls.append((a, k)),
        DEVNULL=subprocess.DEVNULL)
    old_dr3_dir = mod.DR3_DIR
    old_install_dir = mod._install_dir
    old_cwd = Path.cwd()
    try:
        mod.subprocess = fake_subprocess
        mod.DR3_DIR = tmp / "appdata" / "DR3"
        mod._install_dir = lambda: install

        # 1) relaunch child, success -> Popen the new exe (CWD = install dir,
        #    detached, stdio to DEVNULL), no explorer.
        zip_ok = staged / "DR3-windows-9.9.6.zip"
        _make_update_zip(zip_ok, b"new exe")
        rc = mod._run_apply_update(str(zip_ok), "-1", relaunch=True)
        if rc != 0:
            f["n"] += 1
            print(f"FAIL _run_apply_update success path rc={rc}")
        elif len(calls) != 1:
            f["n"] += 1
            print(f"FAIL expected exactly one Popen, got {calls!r}")
        else:
            args, kw = calls[0]
            want_args = ([str(install / "DR3.exe")],)
            if args != want_args or kw.get("cwd") != str(install) \
                    or kw.get("stdin") is not mod.subprocess.DEVNULL \
                    or not kw.get("creationflags"):
                f["n"] += 1
                print(f"FAIL relaunch Popen wrong: args={args!r} kw={kw!r}")
            else:
                print("OK   successful install relaunches the new DR3.exe "
                      "(CWD = install dir, detached)")
        log = mod.DR3_DIR / "update.log"
        if f"relaunched {install / 'DR3.exe'}" not in log.read_text():
            f["n"] += 1
            print("FAIL update.log missing the relaunch note")
        else:
            print("OK   update.log carries the relaunch note")

        # 2) relaunch child, Popen raises -> fall back to Explorer.
        calls.clear()

        def flaky_popen(*a, **k):
            if a and a[0] and str(a[0][0]).endswith("DR3.exe"):
                raise OSError("nope")
            calls.append((a, k))

        mod.subprocess = types.SimpleNamespace(
            Popen=flaky_popen, DEVNULL=subprocess.DEVNULL)
        zip_ok2 = staged / "DR3-windows-9.9.55.zip"
        _make_update_zip(zip_ok2, b"new exe")
        rc = mod._run_apply_update(str(zip_ok2), "-1", relaunch=True)
        if rc != 0:
            f["n"] += 1
            print(f"FAIL fallback path rc={rc}")
        elif calls != [((["explorer", str(install)],), {})]:
            f["n"] += 1
            print(f"FAIL relaunch failure did not fall back to Explorer: {calls!r}")
        else:
            print("OK   relaunch Popen failure falls back to Explorer")
        mod.subprocess = fake_subprocess

        # 3) non-relaunch child (stale zip from an earlier session) -> legacy
        #    explorer behavior.
        calls.clear()
        zip_legacy = staged / "DR3-windows-9.9.5.zip"
        _make_update_zip(zip_legacy, b"new exe")
        rc = mod._run_apply_update(str(zip_legacy), "-1", relaunch=False)
        if rc != 0:
            f["n"] += 1
            print(f"FAIL legacy path rc={rc}")
        elif calls != [((["explorer", str(install)],), {})]:
            f["n"] += 1
            print(f"FAIL stale-zip install should open Explorer: {calls!r}")
        else:
            print("OK   earlier-session zip keeps the legacy Explorer behavior")

        # 4) failed install -> neither relaunch nor explorer.
        calls.clear()
        bad = staged / "DR3-windows-9.9.4.zip"
        bad.write_bytes(b"not a zip")
        rc = mod._run_apply_update(str(bad), "-1", relaunch=True)
        if rc == 0:
            f["n"] += 1
            print("FAIL invalid staged zip should not give rc=0")
        elif calls:
            f["n"] += 1
            print(f"FAIL failed install triggered a Popen: {calls!r}")
        else:
            print("OK   failed install does not relaunch or open Explorer")
    finally:
        mod.subprocess = real_subprocess
        mod.DR3_DIR = old_dr3_dir
        mod._install_dir = old_install_dir
        os.chdir(old_cwd)
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
        for extra in ((), ("relaunch",)):
            proc = subprocess.run(
                [sys.executable, str(dr3_app), "--apply-update",
                 str(tmp / "missing.zip"), "0", *extra],
                capture_output=True, text=True, timeout=60, env=env,
            )
            if proc.returncode != 0:
                f["n"] += 1
                print(f"FAIL --apply-update {extra} in source mode "
                      f"rc={proc.returncode} stderr={proc.stderr[:200]!r}")
        log = tmp / ("appdata" if sys.platform == "win32" else "xdg")
        logs = list(log.rglob("update.log"))
        if not logs or "not an installed DR3.exe bundle" not in logs[0].read_text():
            f["n"] += 1
            print(f"FAIL update.log missing the source-mode note: {logs}")
        else:
            print("OK   --apply-update CLI branch (with/without relaunch arg) "
                  "runs pre-Tk, rc=0 in source mode")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_gui(mod, f):
    import tkinter as tk

    # Local server: /version.json + a real update zip (with .sig) at the
    # download URL.
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    docroot = tmp / "www"
    docroot.mkdir()
    _make_update_zip(docroot / "DR3-windows.zip", b"gui exe", version="9.9.9")
    (docroot / "DR3-windows.zip.sig").write_bytes(_sign_zip(docroot / "DR3-windows.zip"))

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
            try:
                text = str(label.cget("text")) if label is not None else ""
            except tk.TclError:
                # The app closed itself (auto-close fired before this tick) —
                # that IS the new expected end state.
                captured["closed_by_app"] = True
                return
            if "Update v9.9.9 ready" in text or time.time() > deadline:
                captured["final_text"] = text
                captured["final_mapped"] = (
                    label.winfo_ismapped() if label is not None else False)
                captured["final_binding"] = (
                    label.bind("<Button-1>") if label is not None else "")
                self.destroy()
                return
            self.after(50, tick)

        self.after(100, tick)
        orig_mainloop(self)

    # This e2e runs the real mainloop for up to 15 s — long enough for the
    # "start automatically" timer (800 ms) to fire. Keep the test hermetic:
    # serve a config with auto_start_server off so no real server starts.
    orig_load_config = mod.load_config

    def quiet_load_config():
        cfg = orig_load_config()
        cfg["auto_start_server"] = False
        return cfg

    # The auto-close -> _finish_close path spawns a real --apply-update child;
    # point its profile at the temp dir so its update.log stays local. (The
    # GUI process itself already has DR3_DIR computed at import time.)
    env_key = "APPDATA" if sys.platform == "win32" else "XDG_CONFIG_HOME"
    orig_env = os.environ.get(env_key)
    orig_mainloop = tk.Tk.mainloop
    try:
        os.environ[env_key] = str(tmp / "appdata")
        mod.VERSION_CHECK_URL = f"http://127.0.0.1:{port}/version.json"
        mod.UPDATES_DIR = updates
        mod._install_dir = lambda: install_stub
        mod.load_config = quiet_load_config
        tk.Tk.mainloop = fake_mainloop
        mod.run_gui()
    finally:
        if orig_env is None:
            os.environ.pop(env_key, None)
        else:
            os.environ[env_key] = orig_env
        tk.Tk.mainloop = orig_mainloop
        mod.load_config = orig_load_config
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

    if captured.get("closed_by_app"):
        # Even better than the banner: the app closed itself to install.
        print("OK   app closed itself after the download (auto-close fired)")
    else:
        text = str(captured.get("final_text", ""))
        if "Update v9.9.9 ready — closing to install" not in text \
                or not captured.get("final_mapped"):
            f["n"] += 1
            print(f"FAIL banner did not reach ready state, got {text!r}")
        else:
            print("OK   check -> auto-download -> 'ready' banner (end-to-end)")
        if captured.get("final_binding"):
            f["n"] += 1
            print(f"FAIL ready banner is still clickable: "
                  f"{captured['final_binding']!r}")
        else:
            print("OK   ready banner is not clickable (no browser download)")

    staged = list(updates.glob("DR3-windows-*.zip"))
    if len(staged) != 1 or not mod._validate_update_zip(staged[0]):
        f["n"] += 1
        print(f"FAIL staged download missing/invalid: {staged}")
    else:
        print(f"OK   staged zip on disk: {staged[0].name} (valid)")

    shutil.rmtree(tmp, ignore_errors=True)


def check_url_allowlist(mod, f):
    """C3: _update_url_allowed only admits our own domain over https (plus
    loopback for the local e2e server)."""
    cases = [
        ("https://byagersi.com/rally/download/DR3-windows.zip", True),
        ("http://byagersi.com/download/DR3-windows.zip", False),  # plain http
        ("https://evil.example.com/DR3-windows.zip", False),      # foreign host
        ("https://byagersi.com.evil.example/x.zip", False),       # look-alike
        ("http://127.0.0.1:8080/DR3-windows.zip", True),          # local test
        ("https://localhost/x.zip", True),
    ]
    for url, want in cases:
        got = mod._update_url_allowed(url)
        if got is not want:
            f["n"] += 1
            print(f"FAIL _update_url_allowed({url!r}) = {got}, want {want}")
        else:
            print(f"OK   _update_url_allowed({url!r})")


def check_signature(mod, f):
    """C3: _verify_update_signature accepts our key's signature and rejects
    tampered payloads, garbled signatures and truncated ones."""
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    try:
        payload = b"release zip bytes"
        blob = tmp / "blob.bin"
        blob.write_bytes(payload)
        good_sig = _sign_zip(blob)
        if not mod._verify_update_signature(payload, good_sig):
            f["n"] += 1
            print("FAIL valid signature rejected")
        else:
            print("OK   valid Ed25519 signature accepted")
        if mod._verify_update_signature(payload + b"x", good_sig):
            f["n"] += 1
            print("FAIL tampered payload accepted")
        else:
            print("OK   tampered payload rejected")
        bad = bytearray(good_sig)
        bad[0] ^= 0x01
        if mod._verify_update_signature(payload, bytes(bad)):
            f["n"] += 1
            print("FAIL bit-flipped signature accepted")
        else:
            print("OK   bit-flipped signature rejected")
        if mod._verify_update_signature(payload, b"\x00" * 64):
            f["n"] += 1
            print("FAIL all-zero signature accepted")
        else:
            print("OK   all-zero signature rejected")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_install_dir_gate(mod, f):
    """C2: _install_dir only accepts a real bundle — exe named DR3.exe inside
    a folder named DR3 (case-insensitive) carrying a valid INSTALL_MANIFEST
    whose version matches the running exe."""
    tmp = Path(tempfile.mkdtemp(prefix="dr3_upd_"))
    orig_executable = sys.executable
    orig_bundle = mod._is_pyinstaller_bundle
    try:
        mod._is_pyinstaller_bundle = lambda: True
        case_no = 0

        def make_bundle(folder_name: str, version: Optional[str]) -> Path:
            # Unique parent per case — the LEAF name is what the gate checks.
            nonlocal case_no
            root = tmp / f"case{case_no}" / folder_name
            case_no += 1
            (root / "_internal").mkdir(parents=True)
            (root / "DR3.exe").write_bytes(b"x")
            if version is not None:
                manifest = {
                    "version": version,
                    "files": ["DR3.exe", "INSTALL_MANIFEST.json"],
                }
                (root / "INSTALL_MANIFEST.json").write_text(
                    json.dumps(manifest))
            return root

        def expect(folder_name: str, version: Optional[str], want_ok: bool):
            root = make_bundle(folder_name, version)
            sys.executable = str(root / "DR3.exe")
            got = mod._install_dir()
            if want_ok and Path(got) != root:
                f["n"] += 1
                print(f"FAIL _install_dir accepted {root.name} but returned "
                      f"{got!r}")
            elif not want_ok and got is not None:
                f["n"] += 1
                print(f"FAIL _install_dir accepted foreign layout "
                      f"{root.name} (manifest version={version!r})")
            else:
                verdict = "accepted" if want_ok else "rejected"
                detail = "no manifest" if version is None \
                    else f"manifest v{version}"
                print(f"OK   {root.name} ({detail}) {verdict}")

        expect("DR3", None, False)            # no manifest at all
        expect("DR3", "0.0.1", False)         # stale/wrong version
        expect("MyFolder", mod.VERSION, False)  # wrong folder name
        expect("dr3", mod.VERSION, True)      # lowercase folder is fine
        expect("DR3", mod.VERSION, True)      # the real deal
    finally:
        sys.executable = orig_executable
        mod._is_pyinstaller_bundle = orig_bundle
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    mod = _load_dr3_app()
    f = _failures()
    check_versions(mod, f)
    check_staged(mod, f)
    check_validate(mod, f)
    check_url_allowlist(mod, f)
    check_signature(mod, f)
    check_install_dir_gate(mod, f)
    check_download_failure(mod, f)
    check_apply(mod, f)
    check_cwd_swap(mod, f)
    check_relaunch(mod, f)
    check_cli_source_mode(mod, f)
    check_gui(mod, f)
    if f["n"]:
        print(f"\n{f['n']} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
