#!/bin/bash
# Live structure check of the public leaderboard page: mode toggle, all-cars
# option, filters button, car column (only shown when no car is picked) and
# the collapsible extra-filters row. Usage: check_live_leaderboard.sh [URL]
url="${1:-https://byagersi.com/rally/leaderboard}"
html="$(curl -sk --max-time 20 "$url")"
for m in 'class="lb-mode-toggle"' '<option value="">All cars</option>' 'id="lb-more-btn"' '<th>Car</th>' 'lb-extra-row'; do
  printf '%s: %s\n' "$m" "$(printf '%s' "$html" | grep -c -F -- "$m")"
done
