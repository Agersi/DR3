"""Compile-check the embedded python heredocs in bash tools (read-only)."""
import io
import re
import sys

files = [
    'tools/repair_stage_runs_from_stage_stats.sh',
    'tools/seed_best_times.sh',
    'tools/drop_stage_stats.sh',
]
ok = True
for f in files:
    t = io.open(f, encoding='utf-8').read()
    m = re.search(r"<<'EOF'\n(.*?)\nEOF", t, re.S)
    if not m:
        print(f, 'NO HEREDOC FOUND')
        ok = False
        continue
    try:
        compile(m.group(1), f, 'exec')
        print(f, 'COMPILE OK', len(m.group(1).splitlines()), 'lines')
    except SyntaxError as e:
        print(f, 'SYNTAX ERROR line', e.lineno, e.msg)
        ok = False
sys.exit(0 if ok else 1)
