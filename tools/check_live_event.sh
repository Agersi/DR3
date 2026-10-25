#!/bin/bash
curl -sk "https://byagersi.com/rally/clubs/club-120d10dc" > /tmp/c.html
eid=$(grep -o 'event/evt-[a-z0-9]*' /tmp/c.html | head -1 | cut -d/ -f2)
echo "event_id=$eid"
if [ -n "$eid" ]; then
  code=$(curl -sk -o /tmp/e.html -w '%{http_code}' "https://byagersi.com/rally/event/$eid")
  echo "HTTP $code"
  grep -o 'Championship standings\|Round [0-9]* — [^<]*\|Classification\|Stage [0-9]*' /tmp/e.html | sort | uniq -c
fi
