"""Verify the debug.log session archiving in dr3_app.py (backlog #52):

- _archive_debug_log skips when Verbose Logging is off;
- skips an empty log and a header-only log (nothing worth keeping);
- moves a real session's log into logs/ as <version>.<session-start
  timestamp>.debug.log, preserving content and removing the original;
- same-stamp collisions get a -2/-3 suffix;
- the per-launch rotation to debug.prev.log still works as the crash fallback
  (a pre-existing log is rotated at startup, fresh file opened with header);
- archives beyond LOG_ARCHIVE_KEEP are pruned, oldest first.

Exits non-zero on any failure.
"""
import importlib.util
import os
import sys
import tempfile
from datetime import datetime
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


HEADER = "=== DR3 debug.log opened 2026-11-02T14:03:15 ===\n"


def check_archive(mod, f):
    tmp = Path(tempfile.mkdtemp(prefix="dr3_logarch_"))
    orig = (mod.DR3_DIR, mod.LOG_PATH, mod.LOGS_DIR, mod._LOG_OPENED_AT)
    try:
        dr3_dir = tmp / "DR3"
        log_path = dr3_dir / "debug.log"
        logs_dir = dr3_dir / "logs"
        mod.DR3_DIR, mod.LOG_PATH, mod.LOGS_DIR = dr3_dir, log_path, logs_dir
        mod._LOG_OPENED_AT = datetime(2026, 11, 2, 14, 3, 15)

        # 1. verbose off -> skipped, file untouched
        dr3_dir.mkdir(parents=True)
        log_path.write_text(HEADER + "line one\n", encoding="utf-8")
        got = mod._archive_debug_log(False)
        if got is not None or not log_path.exists():
            f["n"] += 1
            print(f"FAIL verbose-off: got {got!r}, file exists={log_path.exists()}")
        else:
            print("OK   verbose off -> no archive, log untouched")

        # 2. empty log -> skipped
        log_path.write_text("", encoding="utf-8")
        if mod._archive_debug_log(True) is not None or not log_path.exists():
            f["n"] += 1
            print("FAIL empty log was archived")
        else:
            print("OK   empty log -> no archive")

        # 3. header-only log -> skipped
        log_path.write_text(HEADER, encoding="utf-8")
        if mod._archive_debug_log(True) is not None or not log_path.exists():
            f["n"] += 1
            print("FAIL header-only log was archived")
        else:
            print("OK   header-only log -> no archive")

        # 4. real session -> moved to logs/<ver>.<stamp>.debug.log
        body = HEADER + "[TELEMETRY] listening on UDP :20999\n[GUI 14:05:00] started\n"
        log_path.write_text(body, encoding="utf-8")
        got = mod._archive_debug_log(True)
        want = logs_dir / f"{mod.VERSION}.20261102-140315.debug.log"
        if got != want or not want.exists() or log_path.exists():
            f["n"] += 1
            print(f"FAIL archive: got {got!r}, want {want!r}, "
                  f"exists={want.exists()}, original gone={not log_path.exists()}")
        elif want.read_text(encoding="utf-8") != body:
            f["n"] += 1
            print("FAIL archive content differs from the session log")
        else:
            print(f"OK   session moved to {want.name} (content preserved)")

        # 5. same-stamp collision -> -2 suffix
        log_path.write_text(HEADER + "second session\n", encoding="utf-8")
        got = mod._archive_debug_log(True)
        want2 = logs_dir / f"{mod.VERSION}.20261102-140315-2.debug.log"
        if got != want2 or not want2.exists():
            f["n"] += 1
            print(f"FAIL collision suffix: got {got!r}, want {want2!r}")
        else:
            print("OK   same-stamp collision gets -2 suffix")

        # 6. retention cap: seed extra archives, oldest pruned first
        keep = mod.LOG_ARCHIVE_KEEP
        for i in range(keep + 3):
            p = logs_dir / f"0.0.{i}.2026010{i % 10}-000000.debug.log"
            p.write_text("x\n", encoding="utf-8")
            t = datetime(2026, 1, i + 1).timestamp()
            os.utime(p, (t, t))
        mod._LOG_OPENED_AT = datetime(2026, 11, 3, 9, 0, 0)
        log_path.write_text(HEADER + "third session\n", encoding="utf-8")
        mod._archive_debug_log(True)
        archives = sorted(logs_dir.glob("*.debug.log"))
        if len(archives) != keep:
            f["n"] += 1
            print(f"FAIL retention cap: {len(archives)} archives, want {keep}")
        else:
            oldest = min(archives, key=lambda p: p.stat().st_mtime).name
            newest = max(archives, key=lambda p: p.stat().st_mtime).name
            if not newest.startswith(f"{mod.VERSION}.20261103-090000"):
                f["n"] += 1
                print(f"FAIL retention cap: newest archive is {newest}")
            else:
                print(f"OK   retention cap keeps {keep} (oldest pruned first)")

        # 7. startup rotation still works (crash fallback)
        out0, err0 = sys.stdout, sys.stderr
        mod._LOG_FILE = None
        try:
            log_path.write_text(HEADER + "crashed session tail\n", encoding="utf-8")
            mod._setup_file_logging()
        finally:
            sys.stdout, sys.stderr = out0, err0
            if mod._LOG_FILE is not None:
                mod._LOG_FILE.close()
                mod._LOG_FILE = None
        prev = dr3_dir / "debug.prev.log"
        fresh_ok = (log_path.exists()
                    and log_path.read_text(encoding="utf-8").startswith(HEADER[:20]))
        if not prev.exists():
            f["n"] += 1
            print("FAIL startup rotation: debug.prev.log missing")
        elif "crashed session tail" not in prev.read_text(encoding="utf-8"):
            f["n"] += 1
            print("FAIL startup rotation: previous content not in debug.prev.log")
        elif not fresh_ok:
            f["n"] += 1
            print("FAIL startup rotation: fresh debug.log not opened with header")
        else:
            print("OK   startup rotation to debug.prev.log intact (crash fallback)")
    finally:
        mod.DR3_DIR, mod.LOG_PATH, mod.LOGS_DIR, mod._LOG_OPENED_AT = orig
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    mod = _load_dr3_app()
    f = _failures()
    check_archive(mod, f)
    if f["n"]:
        print(f"{f['n']} check(s) FAILED")
        return 1
    print("ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
