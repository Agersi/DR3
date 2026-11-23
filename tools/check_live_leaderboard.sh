#!/bin/bash
# Live structure check of the public leaderboard page: mode toggle, all-cars
# option, filters button, car column (only shown when no car is picked) and
# the collapsible extra-filters row. Usage: check_live_leaderboard.sh [URL]
url="${1:-https://byagersi.com/rally/leaderboard}"
css_url="${2:-https://byagersi.com/rally/static/css/style.css}"
html="$(curl -sk --max-time 20 "$url")"
css="$(curl -sk --max-time 20 "$css_url")"
for m in 'class="lb-mode-toggle"' '<option value="">All cars</option>' 'id="lb-more-btn"' '<th>Car</th>' 'class="lb-extra-inner"'; do
  printf '%s: %s\n' "$m" "$(printf '%s' "$html" | grep -c -F -- "$m")"
done
for m in '.lb-filter-row .cselect:has(select[name=stage]), .lb-filter-row .cselect:has(select[name=car]) { width: 24rem; }' '.lb-extra-row { display: grid; grid-template-rows: 0fr; margin-top: 0; transition: grid-template-rows 0.15s ease; }' '.lb-extra-row.open { grid-template-rows: 1fr; }' '.lb-filters-btn { margin: 0; padding: .5rem .4rem; }'; do
  printf '%s: %s\n' "$m" "$(printf '%s' "$css" | grep -c -F -- "$m")"
done
