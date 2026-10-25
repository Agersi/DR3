#!/bin/bash
for u in https://byagersi.com/ https://byagersi.com/rally https://byagersi.com/rally/ http://127.0.0.1:3000/ http://127.0.0.1:3001/; do
  printf '%s ' "$u"; curl -sk -o /dev/null --max-time 15 -w '%{http_code}\n' -L "$u"
done
ss -ltn | grep -E ':(80|443|3000|3001|8001)\s'
systemctl is-active nginx pm2-agersi mariadb dr3-web cron
