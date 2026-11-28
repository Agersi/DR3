"""Verify the uninstall link + _install_dir() guards (backlog #33a).

Run from dr3/:  .venv-build\\Scripts\\python.exe ..\\tools\\check_uninstall.py
Boots the real GUI with mainloop patched out (no server start, no clicks).
"""
from __future__ import annotations

import sys
from pathlib import Path

DR3 = Path(__file__).resolve().parents[1] / "dr3"
sys.path.insert(0, str(DR3))

import dr3_app  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


# 1. _install_dir guards
check("source run: _install_dir() is None", dr3_app._install_dir() is None,
      str(dr3_app._install_dir()))

fake_root = Path(r"C:\fake\DR3")
orig_exe = dr3_app.sys.executable
orig_frozen = getattr(dr3_app.sys, "frozen", False)
try:
    dr3_app.sys.frozen = True
    dr3_app.sys.executable = str(fake_root / "DR3.exe")
    check("bundled DR3.exe: _install_dir() is exe parent",
          dr3_app._install_dir() == fake_root, str(dr3_app._install_dir()))
    dr3_app.sys.executable = str(fake_root / "something-else.exe")
    check("bundled non-DR3.exe: _install_dir() is None",
          dr3_app._install_dir() is None, str(dr3_app._install_dir()))
finally:
    dr3_app.sys.frozen = orig_frozen
    dr3_app.sys.executable = orig_exe

# 2. GUI boots; uninstall link present on the Main tab footer
import tkinter as tk  # noqa: E402

seen: dict = {}


def fake_mainloop(self):
    self.update_idletasks()
    seen["root"] = self


tk.Tk.mainloop = fake_mainloop
dr3_app.run_gui()

root = seen.get("root")
check("GUI booted", root is not None)
if root is not None:
    widgets: list = []

    def walk(w):
        for c in w.winfo_children():
            widgets.append(c)
            walk(c)

    walk(root)
    labels = [w for w in widgets if isinstance(w, tk.Label) and w.cget("text") == "Uninstall DR3"]
    check("'Uninstall DR3' link on Main tab", len(labels) == 1, f"count={len(labels)}")
    root.destroy()

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    raise SystemExit(1)
print("ALL UNINSTALL CHECKS PASSED")
