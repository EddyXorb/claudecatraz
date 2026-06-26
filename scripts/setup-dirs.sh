#!/usr/bin/env bash
# Legt die Bind-Mount-Verzeichnisse neben dem Compose-File an und gibt die
# Schreibziele dem Container-User (DEV_UID), damit der non-root-Service mit
# read-only Root-FS hineinschreiben kann.
# Siehe docs/design/agentic-workflow/README.md §11.6.
set -euo pipefail

cd "$(dirname "$0")/.."

# .env laden, falls vorhanden (für DEV_UID)
[ -f .env ] && set -a && . ./.env && set +a
DEV_UID="${DEV_UID:-1000}"

mkdir -p config state/warden logs/warden logs/squid workspace claude

# Schreibziele dem Service-User geben; config/ bleibt dem Host-Editor (read-only gemountet)
chown -R "$DEV_UID" state logs 2>/dev/null || \
  echo "Hinweis: chown auf state/ logs/ uebersprungen (ggf. mit sudo ausfuehren)."

echo "Verzeichnisse bereit: config/ state/ logs/ workspace/ claude/  (DEV_UID=$DEV_UID)"
