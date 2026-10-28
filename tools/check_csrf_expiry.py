"""Verify long-lived forms never hit "The CSRF token has expired".

The championship builder (new -> preview -> edit -> submit) can stay open far
longer than Flask-WTF's default 1h WTF_CSRF_TIME_LIMIT; Config sets the limit
to None so a stale form still submits. This check proves both sides: a 2h-old
token validates under the app config, and the same token is rejected as
expired when the old 1h limit is forced (negative control).

Run from dr3/web:  .venv\\Scripts\\python.exe ..\\tools\\check_csrf_expiry.py
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "dr3" / "web"
sys.path.insert(0, str(WEB))

DB_PATH = WEB / "csrf_check.db"
if DB_PATH.exists():
    DB_PATH.unlink()

os.environ["SECRET_KEY"] = "csrf-check-key"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ.pop("DR3_DISABLE_CSRF", None)  # CSRF must be ON for this check
os.environ.pop("SESSION_COOKIE_SECURE", None)

from app import create_app  # noqa: E402
from flask_wtf.csrf import generate_csrf, validate_csrf  # noqa: E402
from wtforms import ValidationError  # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


app = create_app()

check("Config disables the CSRF clock limit",
      app.config.get("WTF_CSRF_TIME_LIMIT") is None,
      str(app.config.get("WTF_CSRF_TIME_LIMIT")))

# Forge a token stamped 2h in the past: patch time.time only while signing,
# then validate within the SAME request context (the session is per-request)
# at the real "now", i.e. 2h after the stamp.
real_time = time.time
old_now = real_time() - 2 * 3600
with app.test_request_context("/login"):
    time.time = lambda: old_now
    try:
        stale_token = generate_csrf()
    finally:
        time.time = real_time

    # 1. Under the app config (limit None) the stale token must validate.
    ok, err = True, ""
    try:
        validate_csrf(stale_token)
    except ValidationError as e:
        ok, err = False, str(e)
    check("2h-old token validates with WTF_CSRF_TIME_LIMIT=None", ok, err)

    # 2. Negative control: the same token under the old default (3600s) must
    #    be rejected as expired — proves the token really is stale.
    expired = False
    try:
        validate_csrf(stale_token, time_limit=3600)
    except ValidationError as e:
        expired = "expired" in str(e).lower()
    check("negative control: 2h-old token rejected under the old 1h limit",
          expired)

# 3. E2E: a real form POST carrying a stale token no longer 400s.
client = app.test_client()
r = client.get("/login")
m = re.search(r'name="csrf_token" value="([^"]+)"', r.get_data(as_text=True))
token = m.group(1) if m else None
check("login page renders a csrf token", bool(token))

time.time = lambda: real_time() + 2 * 3600  # "now" is 2h after the form rendered
try:
    r = client.post("/login", data={
        "csrf_token": token, "username": "nobody-here", "password": "wrong-pass",
    }, follow_redirects=True)  # bad creds flash+redirect to /login (200); CSRF failure would be a 400
finally:
    time.time = real_time

body = r.get_data(as_text=True)
check("stale-token login POST is not a 400 Bad Request", r.status_code == 200,
      f"status={r.status_code}")
check("...and the failure is a normal form error, not CSRF expiry",
      "Invalid username or password" in body and "expired" not in body.lower())

print()
if failures:
    print(f"{len(failures)} check(s) FAILED")
    sys.exit(1)
print("ALL CHECKS PASSED")
