"""Measure how long 'DR3.SingleInstance.Mutex' remains visible in the kernel
object namespace after the holding process is killed and reaped.

Hypothesis: Popen.wait() returns as soon as the process object is signaled,
but the holder's handles (and thus the named mutex) may linger for a short
window — which makes an immediate CreateMutexW see ERROR_ALREADY_EXISTS.
"""
import ctypes
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

k = ctypes.windll.kernel32
DR3_DIR = Path(r"D:\CODE\DR2\dr3")
DR3_APP = DR3_DIR / "dr3_app.py"


def load_mod():
    sys.path.insert(0, str(DR3_DIR))
    spec = importlib.util.spec_from_file_location("dr3_app", DR3_APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load_mod()
tmp = Path(tempfile.mkdtemp(prefix="dr3_race_"))
env = dict(os.environ)
env["APPDATA"] = str(tmp / "appdata")

holder_src = (
    "import importlib.util, sys, time\n"
    f"sys.path.insert(0, {str(DR3_DIR)!r})\n"
    "spec = importlib.util.spec_from_file_location('dr3_app', "
    f"{str(DR3_APP)!r})\n"
    "mod = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(mod)\n"
    "ok = mod._acquire_single_instance_lock()\n"
    "print('HOLDER' + ('OK' if ok else 'FAIL'), flush=True)\n"
    "time.sleep(60)\n"
)

holder = subprocess.Popen([sys.executable, "-c", holder_src],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env)
assert "HOLDEROK" in holder.stdout.readline().strip()
print("holder up")

t0 = time.perf_counter()
holder.kill()
t1 = time.perf_counter()
holder.wait(timeout=10)
t2 = time.perf_counter()
print(f"kill took {(t1 - t0) * 1000:.1f} ms; wait returned after "
      f"{(t2 - t1) * 1000:.1f} ms")

# Probe immediately, then every 50 ms: does the mutex still exist?
for i in range(20):
    h = k.CreateMutexW(None, False, "DR3.SingleInstance.Mutex")
    gle = k.GetLastError()
    exists = (gle == 183)
    if h:
        k.CloseHandle(h)
    print(f"t+{(time.perf_counter() - t2) * 1000:6.1f} ms: "
          f"{'STILL EXISTS' if exists else 'free'}")
    if not exists:
        break
    time.sleep(0.05)
