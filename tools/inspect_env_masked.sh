#!/bin/bash
# Masked .env inspector (S5): prints key names + diagnostics only, never values.
# Flags that indicate a corrupted value: INLINE_HASH (trailing comment absorbed
# into the value by the app's parser) or TRAILING_SPACE.
ENV="${1:-/opt/dr3/web/.env}"
awk '
{
  eq = index($0, "=")
  if (eq == 0 || $0 ~ /^[[:space:]]*#/) next
  k = substr($0, 1, eq - 1)
  v = substr($0, eq + 1)
  hash = (index(v, "#") > 0) ? "INLINE_HASH" : "-"
  sp   = (v ~ /[[:space:]]$/) ? "TRAILING_SPACE" : "-"
  printf "%-24s len=%-4d %s %s\n", k, length(v), hash, sp
}' "$ENV"
