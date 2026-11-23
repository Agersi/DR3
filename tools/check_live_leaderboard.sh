#!/bin/bash
# Live structure check of the public leaderboard page: mode toggle, all-cars
# option, filters button, car column (only shown when no car is picked) and
# the collapsible extra-filters row. Usage: check_live_leaderboard.sh [URL]
url="${1:-https://byagersi.com/rally/leaderboard}"
css_url="${2:-https://byagersi.com/rally/static/css/style.css}"
html="$(curl -sk --max-time 20 "$url")"
css="$(curl -sk --max-time 20 "$css_url")"
for m in 'class="lb-mode-toggle"' '<option value="">All cars</option>' 'value="class-' 'id="lb-more-btn"' '<th>Car</th>' 'class="lb-extra-inner"'; do
  printf '%s: %s\n' "$m" "$(printf '%s' "$html" | grep -c -F -- "$m")"
done
for m in '.lb-filter-row .cselect:has(select[name=stage]), .lb-filter-row .cselect:has(select[name=car]) { width: 24rem; }' '.lb-extra-row { display: grid; grid-template-rows: 0fr; margin-top: 0; transition: grid-template-rows 0.15s ease; }' '.lb-extra-row.open { grid-template-rows: 1fr; }' '.lb-filters-btn { margin: 0 0 .2rem 0; padding: .2rem .53rem; align-self: flex-end; font-size: .9rem; }' '.lb-filters .cselect-btn { position: relative; width: 100%; padding: 0.5rem 2.1rem 0.5rem 0.6rem;' ; do
  printf '%s: %s\n' "$m" "$(printf '%s' "$css" | grep -c -F -- "$m")"
done
cid="$(printf '%s' "$html" | grep -o 'value="class-[0-9]*"' | head -1 | sed 's/[^0-9]//g')"
if [ -n "$cid" ]; then
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 "$url?car=class-$cid")"
  printf 'GET ?car=class-%s: %s\n' "$cid" "$code"
fi
