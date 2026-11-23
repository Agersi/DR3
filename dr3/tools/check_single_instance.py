"""Verify DR3's single-instance behavior in dr3_app.py:

- the lock is process-scoped: a child python process that acquires it blocks
  the parent's acquire, and killing the child frees it (Windows named mutex /
  Linux flock);
- while the lock is held, a second in-process acquire fails;
- _focus_existing_instance() is best-effort: with no DR3 window around it
  returns False without crashing;
- end-to-end: with the lock held in-process, launching `python dr3_app.py`
  as a real subprocess exits 0, prints the focus notice, and never creates
  debug.log (the check runs before _setup_file_logging).

Run with no other DR3 instance open (a live one would hold the mutex).
Exits non-zero on any failure.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _load_dr3_app():
    path = Path(__file__).resolve().parent.parent / "dr3_app.py"
    sys.path.insert(0, str(path.parent))  # dr2server/ package next to dr3_app.py
    spec = importlib.util.spec_from_file_location("dr3_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _profile_env(tmp: Path) -> dict:
    env = dict(os.environ)
    if sys.platform == "win32":
        env["APPDATA"] = str(tmp / "appdata")
    else:
        env["XDG_CONFIG_HOME"] = str(tmp / "xdg")
    return env


def main() -> int:
    mod = _load_dr3_app()
    dr3_dir = Path(__file__).resolve().parent.parent
    dr3_app = dr3_dir / "dr3_app.py"
    tmp = Path(tempfile.mkdtemp(prefix="dr3_si_"))
    failures = 0

    # --- Cross-process: a child holding the lock blocks us; killing it frees it ---
    holder_src = (
        "import importlib.util, sys, time\n"
        f"sys.path.insert(0, {str(dr3_dir)!r})\n"
        "spec = importlib.util.spec_from_file_location('dr3_app', "
        f"{str(dr3_app)!r})\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "ok = mod._acquire_single_instance_lock()\n"
        "print('HOLDER' + ('OK' if ok else 'FAIL'), flush=True)\n"
        "time.sleep(60)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_src],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_profile_env(tmp),
    )
    try:
        line = holder.stdout.readline().strip()
        if "HOLDEROK" not in line:
            print(f"FAIL child could not acquire the lock: {line!r}")
            return 1
        print("OK   child process acquires the lock")

        if mod._acquire_single_instance_lock():
            print("FAIL parent acquire must fail while the child holds the lock")
            failures += 1
        else:
            print("OK   parent acquire fails while another process holds the lock")

        holder.kill()
        holder.wait(timeout=10)

        if not mod._acquire_single_instance_lock():
            print("FAIL lock must be free again once the holding process exits")
            failures += 1
        else:
            print("OK   lock is free again after the holding process exits")
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=10)

    # --- In-process: a second acquire while we hold it fails ---
    if mod._acquire_single_instance_lock():
        print("FAIL second in-process acquire must fail while the lock is held")
        failures += 1
    else:
        print("OK   second in-process acquire fails while the lock is held")

    # --- Focus fallback: no DR3 window around -> False, no crash ---
    try:
        found = mod._focus_existing_instance()
    except Exception as exc:
        print(f"FAIL _focus_existing_instance crashed: {exc!r}")
        failures += 1
    else:
        if found:
            print("INFO a live DR3 window was found and focused (test still valid)")
        else:
            print("OK   _focus_existing_instance returns False with no DR3 window")

    # --- End-to-end: duplicate launch exits 0, focuses, never touches debug.log ---
    env = _profile_env(tmp)
    proc = subprocess.run(
        [sys.executable, str(dr3_app)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    profile = tmp / ("appdata" if sys.platform == "win32" else "xdg")
    logs = list(profile.rglob("debug.log")) if profile.exists() else []
    if proc.returncode != 0:
        print(f"FAIL duplicate launch rc={proc.returncode} "
              f"stdout={proc.stdout[:200]!r} stderr={proc.stderr[:200]!r}")
        failures += 1
    elif "[DR3] another instance is already running" not in proc.stdout:
        print(f"FAIL duplicate launch missing the focus notice: "
              f"stdout={proc.stdout[:200]!r}")
        failures += 1
    else:
        print("OK   duplicate launch prints the focus notice and exits 0")
    if logs:
        print(f"FAIL duplicate launch created debug.log: {logs}")
        failures += 1
    else:
        print("OK   duplicate launch never creates debug.log (pre-logging check)")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
