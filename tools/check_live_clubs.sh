#!/bin/bash
for c in club-120d10dc club-1bae6e2f club-212d17ff; do
  code=$(curl -sk -o /tmp/c.html -w '%{http_code}' "https://byagersi.com/rally/clubs/$c")
  st=$(grep -c '<h3>Standings</h3>' /tmp/c.html)
  nr=$(grep -c 'round-' /tmp/c.html)
  echo "$c -> HTTP $code standings_sections=$st round_links=$nr"
done
