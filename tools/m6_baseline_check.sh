#!/bin/bash
for u in https://byagersi.com/rollerbot https://byagersi.com/phpmyadmin https://byagersi.com/server/ https://byagersi.com/download_rb/; do
  printf '%s ' "$u"; curl -sk -o /dev/null --max-time 15 -w '%{http_code}\n' -L "$u"
done
