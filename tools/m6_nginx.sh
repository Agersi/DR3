#!/bin/bash
set -e
cp /etc/nginx/sites-available/byagersi.com /etc/nginx/sites-available/byagersi.com.bak-m6
python3 - <<'PYEOF'
path = '/etc/nginx/sites-available/byagersi.com'
s = open(path).read()
anchor = "    # Main location\n"
addition = """    # DR3 web backend (Flask/gunicorn on 127.0.0.1:8001) — M6 additive
    location = /rally {
        return 301 /rally/;
    }

    location /rally/ {
        proxy_pass http://127.0.0.1:8001/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

"""
assert anchor in s, "anchor not found"
assert 'location /rally/' not in s, "already present"
s = s.replace(anchor, addition + anchor)
open(path, 'w').write(s)
print("INSERTED")
PYEOF
nginx -t
systemctl reload nginx
echo RELOADED
