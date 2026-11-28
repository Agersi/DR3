"""Scan the DR3 client debug.log for lines matching keywords / time windows.

Usage:
  python tools/debug_log_scan.py [KEYWORD ...] [--after HH:MM:SS] [--before HH:MM:SS]
                                 [--log PATH] [--stream]
Without keywords, prints all non-STREAM lines in the window. --stream includes
STREAM tick lines.
"""
import argparse
import os
import re


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keywords", nargs="*")
    ap.add_argument("--after", default=None)
    ap.add_argument("--before", default=None)
    ap.add_argument("--log", default=os.path.join(os.environ.get("APPDATA", ""), "DR3", "debug.log"))
    ap.add_argument("--stream", action="store_true", help="include STREAM tick lines")
    args = ap.parse_args()

    data = open(args.log, "rb").read()
    txt = data.decode("utf-8", errors="replace")
    ts_re = re.compile(r"(\d{2}:\d{2}:\d{2})")
    for ln in txt.splitlines():
        m = ts_re.search(ln)
        if not m:
            continue
        t = m.group(1)
        if args.after and t < args.after:
            continue
        if args.before and t > args.before:
            continue
        if not args.stream and "STREAM" in ln:
            continue
        if args.keywords and not any(k in ln for k in args.keywords):
            continue
        print(ln[:400])


if __name__ == "__main__":
    main()
