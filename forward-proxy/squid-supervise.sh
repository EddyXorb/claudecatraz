#!/bin/sh
# PID 1 of the forward-proxy: runs squid and applies config edits live.
# A changed file is applied once it is unchanged for a full poll: valid
# (squid -k parse) -> SIGHUP reconfigure; invalid -> squid stopped, no egress.
# Each decision lands in $CONFIG_STATUS_FILE for the host CLI.
set -u

SQUID=${SQUID_BIN:-squid}
CONF=${SQUID_CONF:-/etc/squid/squid.conf}
WATCH=${SQUID_WATCH:-"/etc/squid/squid.conf /etc/squid/allowlist.txt"}
POLL=${CONFIG_POLL_S:-1}
STATUS=${CONFIG_STATUS_FILE:-/var/log/squid/config-status}

pid=""

# Same algorithm as the host CLI: sha256 over one per-file sha256 line ("-" if unreadable).
fingerprint() {
  for f in $WATCH; do
    if [ -r "$f" ]; then sha256sum < "$f" | cut -d' ' -f1; else echo -; fi
  done | sha256sum | cut -d' ' -f1
}

write_status() {
  detail=$(printf '%s' "$3" | tr '\n\t' '  ')
  printf 'state=%s\nfingerprint=%s\ndetail=%s\n' "$1" "$2" "$detail" > "$STATUS.tmp" \
    && mv -f "$STATUS.tmp" "$STATUS" \
    || echo "squid-supervise: cannot write $STATUS" >&2
}

stop_squid() {
  if [ -n "$pid" ]; then
    kill -TERM "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null
    pid=""
  fi
}

apply() {
  if out=$("$SQUID" -k parse -f "$CONF" 2>&1); then
    if [ -n "$pid" ]; then
      kill -HUP "$pid"
    else
      "$SQUID" -N -d1 -f "$CONF" &
      pid=$!
    fi
    write_status applied "$1" ""
    echo "squid-supervise: config applied ($1)" >&2
  else
    stop_squid
    reason=$(printf '%s\n' "$out" | grep -E 'FATAL|ERROR' | tail -n1)
    [ -n "$reason" ] || reason=$(printf '%s\n' "$out" | tail -n1)
    write_status rejected "$1" "$reason"
    echo "squid-supervise: CONFIG REJECTED — all egress denied until fixed: $reason" >&2
  fi
}

trap 'stop_squid; exit 0' TERM INT

applied=$(fingerprint)
apply "$applied"
pending=""
while :; do
  sleep "$POLL" &
  wait $!
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid"
    rc=$?
    echo "squid-supervise: squid exited ($rc)" >&2
    [ "$rc" -gt 0 ] || rc=1
    exit "$rc"
  fi
  fp=$(fingerprint)
  if [ "$fp" = "$applied" ]; then
    pending=""
  elif [ "$fp" != "$pending" ]; then
    pending=$fp
  else
    pending=""
    applied=$fp
    apply "$fp"
  fi
done
