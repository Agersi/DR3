"""End-to-end verification of the deployed DR3 site (https://byagersi.com/rally).

Runs fully locally; uses tools/vps.py over SSH only to read the email-verify
link out of gunicorn logs (SMTP not configured yet -> dev log fallback).
"""
import os
import re
import secrets
import subprocess
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = 'https://byagersi.com/rally'
failures: list[str] = []


def check(name, cond, extra=''):
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra and not cond else ''))
    if not cond:
        failures.append(name)


def csrf_of(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def vps_sudo(cmd):
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, 'tools', 'vps.py'), '--sudo', cmd],
        capture_output=True, text=True, timeout=120,
    )
    return out.stdout + out.stderr


def cron_key():
    with open(os.path.join(ROOT, 'build', 'web.env'), encoding='utf-8') as f:
        for line in f:
            if line.startswith('CRON_API_KEY='):
                return line.strip().split('=', 1)[1]
    raise SystemExit('CRON_API_KEY not found in build/web.env')


def main():
    s = requests.Session()
    suffix = secrets.token_hex(4)
    username = f'e2e_{suffix}'
    password = 'E2e-Passw0rd!'
    email = f'{username}@example.com'

    r = s.get(BASE + '/')
    check('home renders', r.status_code == 200 and 'Dirt Rally' in r.text, str(r.status_code))
    r = s.get(BASE + '/events')
    check('official events page removed', r.status_code == 404, str(r.status_code))
    r = s.get(BASE + '/install')
    check('install page', r.status_code == 200, str(r.status_code))

    r = s.get(BASE + '/register')
    tok = csrf_of(r.text)
    check('register form csrf', tok is not None)
    r = s.post(BASE + '/register', data={
        'csrf_token': tok, 'username': username, 'email': email,
        'password': password, 'confirm': password,
        'display_name': f'E2E Bot {suffix}', 'country': 'SE',
    }, headers={'Referer': BASE + '/register'}, allow_redirects=False)
    check('register accepted', r.status_code in (302, 303), str(r.status_code))

    # SMTP not configured yet, so the verify link isn't emailed. Read the
    # throwaway account's one-time verify_token from the dedicated dr3 DB to
    # exercise the /verify_email endpoint (no secrets are logged by the app).
    log = vps_sudo(
        f"mysql -N dr3 -e \"SELECT verify_token FROM users WHERE username='{username}'\""
        " | grep -oE '[A-Za-z0-9_-]{43}'")
    m = re.search(r'([A-Za-z0-9_-]{43})', log)
    check('verify token fetched from db', m is not None, log[-200:])
    if not m:
        return finish()
    r = s.get(f'{BASE}/verify_email/{m.group(1)}')
    check('email verified via endpoint', 'welcome' in r.text.lower(), r.text[:200])

    r = s.get(BASE + '/login')
    tok = csrf_of(r.text)
    r = s.post(BASE + '/login', data={'csrf_token': tok, 'username': username, 'password': password},
               headers={'Referer': BASE + '/login'}, allow_redirects=False)
    check('login', r.status_code in (302, 303), str(r.status_code))

    def generate_token():
        page = s.get(BASE + '/account')
        tok = csrf_of(page.text)
        r = s.post(BASE + '/api/token/generate', data={'csrf_token': tok},
                   headers={'Referer': BASE + '/account'}, allow_redirects=False)
        check('token generate redirects', r.status_code in (302, 303), str(r.status_code))
        page = s.get(BASE + '/dashboard')
        m = re.search(r'df_[A-Za-z0-9_-]{30,}', page.text)
        return m.group(0) if m else None

    token = generate_token()
    check('token on dashboard', token is not None)
    if not token:
        return finish()
    new_token = generate_token()
    check('regenerated token replaces old', new_token is not None and new_token != token)
    if new_token:
        token = new_token

    gh = {'Authorization': f'Bearer {token}'}
    r = s.get(BASE + '/api/game/token-test', headers=gh)
    check('game token-test', r.status_code == 200 and r.json().get('ok') is True, r.text[:120])
    r = s.get(BASE + '/api/game/profile', headers=gh)
    check('game profile', r.status_code == 200 and r.json().get('username') == username, r.text[:120])
    r = s.get(BASE + '/api/game/clubs', headers=gh)
    check('game clubs', r.status_code == 200 and r.json().get('ok') is True, r.text[:120])
    r = s.post(BASE + '/api/game/auth', json={'steam_name': f'E2E Bot {suffix}'})
    check('game steam auth link', r.status_code == 200 and r.json().get('linked') is True, r.text[:120])

    cr = requests.post(f'{BASE}/api/cron', headers={'X-Cron-Key': cron_key()})
    cj = cr.json() if cr.status_code == 200 else {}
    check('cron tick accepted + no generated official events',
          cr.status_code == 200 and cj.get('ok') is True and cj.get('created') == [],
          f'{cr.status_code} {cr.text[:120]}')

    r = s.get(BASE + '/api/game/challenges', headers=gh)
    check('official challenges endpoint removed', r.status_code == 404, str(r.status_code))
    for path in ('/api/game/time-trial-submit',
                 '/api/game/time-trial-leaderboard?vclass=101&track=10&conditions=5',
                 '/api/game/time-trial-leaderboard-id?vclass=101&track=10&conditions=5&category=1'):
        r = s.get(BASE + path, headers=gh)
        check(f'removed endpoint {path.split("?")[0]}', r.status_code == 404, str(r.status_code))

    r = s.head(BASE + '/download/DR3-windows.zip')
    check('client zip served', r.status_code == 200 and int(r.headers.get('Content-Length', 0)) > 10_000_000,
          f"{r.status_code} len={r.headers.get('Content-Length')}")

    return finish()


def finish():
    print()
    if failures:
        print(f'E2E FAILED: {len(failures)} check(s): {failures}')
        sys.exit(1)
    print('ALL E2E CHECKS PASSED')


if __name__ == '__main__':
    main()
