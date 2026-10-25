#!/bin/bash
set -e
tar -xzf /tmp/web.tar.gz -C /opt/dr3
tar -xzf /tmp/dr2server.tar.gz -C /opt/dr3
chown -R agersi:agersi /opt/dr3
python3 -m venv /opt/dr3/.venv
/opt/dr3/.venv/bin/pip install --quiet --upgrade pip
/opt/dr3/.venv/bin/pip install --quiet flask flask-wtf flask-sqlalchemy gunicorn pymysql
/opt/dr3/.venv/bin/python -c "import flask, flask_wtf, flask_sqlalchemy, gunicorn, pymysql; print('DEPS_OK')"
ls /opt/dr3
rm -f /tmp/web.tar.gz /tmp/dr2server.tar.gz
