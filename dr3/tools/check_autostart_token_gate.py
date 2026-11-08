"""Verify the "Start automatically when DR3 opens" option in dr3_app.py:

- off by default on a fresh install (auto_start_server absent -> unchecked);
- cannot be enabled until a game token is configured (checkbox disabled and
  forced off while there's no token);
- once a token is present the checkbox unlocks, and with auto_start_server=True
  it reads checked.

Boots the real GUI three times under controlled configs (mainloop patched to
capture the root only, so no event loop runs and no server is ever started)
and asserts the checkbox state + checked value. Exits non-zero on any failure.
"""
import importlib.util
import sys
import tkinter as tk
from pathlib import Path

AUTO_START_TEXT = "Start automatically when DR3 opens"


def _load_dr3_app():
    path = Path(__file__).resolve().parent.parent / "dr3_app.py"
    sys.path.insert(0, str(path.parent))  # dr2server/ package next to dr3_app.py
    spec = importlib.util.spec_from_file_location("dr3_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _find_auto_start_cb(root):
    """Return the auto-start Checkbutton (or None)."""
    for w in _walk(root):
        if w.winfo_class() == "Checkbutton" \
                and str(w.cget("text")) == AUTO_START_TEXT:
            return w
    return None


def _boot(mod, cfg):
    """Boot run_gui() with a controlled config.

    Returns (root, auto_var) where auto_var is the real BooleanVar backing the
    "Start automatically" checkbox (captured at construction time). The mainloop
    is patched to capture the root only, so no event loop runs and no server is
    ever started.
    """
    captured = {}

    def fake_mainloop(self, n=0):
        captured["root"] = self  # capture only; never enter the real loop

    orig_load_config = mod.load_config
    orig_save_config = mod.save_config
    orig_mainloop = tk.Tk.mainloop
    orig_checkbutton = tk.Checkbutton

    def tracking_checkbutton(*args, **kwargs):
        w = orig_checkbutton(*args, **kwargs)
        if str(kwargs.get("text", "")) == AUTO_START_TEXT:
            captured["auto_var"] = kwargs.get("variable")
        return w

    mod.load_config = lambda: dict(cfg)
    mod.save_config = lambda c: None  # hermetic: never touch the real config file
    tk.Tk.mainloop = fake_mainloop
    tk.Checkbutton = tracking_checkbutton
    try:
        mod.run_gui()
    finally:
        mod.load_config = orig_load_config
        mod.save_config = orig_save_config
        tk.Tk.mainloop = orig_mainloop
        tk.Checkbutton = orig_checkbutton
    return captured.get("root"), captured.get("auto_var")


def _state_of(cb):
    return str(cb.cget("state"))


def _checked(auto_var):
    return bool(auto_var.get()) if auto_var is not None else False


def main() -> int:
    mod = _load_dr3_app()
    failures = 0

    # 1) Fresh install: no token, auto_start_server absent.
    root, auto_var = _boot(mod, {"api_url": "https://byagersi.com/rally"})
    cb = _find_auto_start_cb(root) if root is not None else None
    if cb is None:
        failures += 1
        print("FAIL no-token boot: auto-start checkbox not found")
    else:
        st, chk = _state_of(cb), _checked(auto_var)
        if st != "disabled":
            failures += 1
            print(f"FAIL no token: checkbox should be disabled, is {st}")
        else:
            print("OK   no token: auto-start checkbox is disabled")
        if chk:
            failures += 1
            print("FAIL no token: auto-start should be off by default")
        else:
            print("OK   no token: auto-start is off by default")

    # 2) Token set, auto_start_server absent -> enabled but unchecked (opt-in).
    root, auto_var = _boot(mod, {"api_url": "https://byagersi.com/rally",
                                 "game_token": "df_testtoken"})
    cb = _find_auto_start_cb(root) if root is not None else None
    if cb is None:
        failures += 1
        print("FAIL token boot: auto-start checkbox not found")
    else:
        st, chk = _state_of(cb), _checked(auto_var)
        if st != "normal":
            failures += 1
            print(f"FAIL token set: checkbox should be enabled, is {st}")
        else:
            print("OK   token set: auto-start checkbox is enabled")
        if chk:
            failures += 1
            print("FAIL token set (no flag): checkbox should stay unchecked")
        else:
            print("OK   token set (no flag): stays off until opted in")

    # 3) Token set + auto_start_server=True -> enabled and checked.
    root, auto_var = _boot(mod, {"api_url": "https://byagersi.com/rally",
                                 "game_token": "df_testtoken",
                                 "auto_start_server": True})
    cb = _find_auto_start_cb(root) if root is not None else None
    if cb is None:
        failures += 1
        print("FAIL token+flag boot: auto-start checkbox not found")
    else:
        st, chk = _state_of(cb), _checked(auto_var)
        if st != "normal":
            failures += 1
            print(f"FAIL token+flag: checkbox should be enabled, is {st}")
        if not chk:
            failures += 1
            print("FAIL token+flag: checkbox should be checked")
        if st == "normal" and chk:
            print("OK   token set + flag on: auto-start enabled and checked")

    if failures:
        print(f"\n{failures} check(s) FAILED")
        return 1
    print("\nALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
