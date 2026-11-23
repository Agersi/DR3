#!/bin/bash
# Live select-state check: pick a real car option from the leaderboard page,
# request it back (?car=<vid>), and print every option rendered `selected` —
# used to debug "the car select resets after selecting an option".
# Usage: check_live_select_state.sh [URL]
set -e
url="${1:-https://byagersi.com/rally/leaderboard}"
python3 - "$url" <<'PYEOF'
import re, sys, urllib.request

url = sys.argv[1]
html = urllib.request.urlopen(url, timeout=20).read().decode('utf-8')

car_block = re.search(r'<select name="car">(.*?)</select>', html, re.S)
assert car_block, 'car select not found'
opts = re.findall(r'<option value="([^"]*)"', car_block.group(1))
vid = next(v for v in opts if v.isdigit())  # first real car (skip "All cars")

def probe(tag, carval):
    page = urllib.request.urlopen(f'{url}?car={carval}', timeout=20).read().decode('utf-8')
    block = re.search(r'<select name="car">(.*?)</select>', page, re.S)
    # strict: `selected` must sit directly before `>` — a stray char in
    # between (e.g. `selected}`) is NOT a real selected attribute to browsers
    sel_in_car = re.findall(r'<option value="([^"]*)" selected>([^<]*)', block.group(1)) if block else None
    broken = re.findall(r'<option [^>]*selected[^>]*?}>', page) if block else []
    print(f'--- probing {tag} (car={carval})')
    print('selected inside car select:', sel_in_car or 'NONE')
    if broken:
        print('BROKEN selected attrs (stray char before >):', len(broken))

probe('specific car', vid)
class_val = next(v for v in opts if v.startswith('class-'))
probe('class option', class_val)
PYEOF
