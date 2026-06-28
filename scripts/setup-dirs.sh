#!/usr/bin/env bash
# Creates the bind-mount directories alongside the Compose file and grants
# write targets to the container user (DEV_UID) so that the non-root service
# with a read-only root FS can write to them.
# See docs/design/agentic-workflow/README.md §11.6.
set -euo pipefail

cd "$(dirname "$0")/.."

# Load .env if present (for DEV_UID)
[ -f .env ] && set -a && . ./.env && set +a
DEV_UID="${DEV_UID:-1000}"

mkdir -p config state/warden logs/warden logs/squid workspace claude

# Grant write targets to the service user; config/ stays with the host editor (mounted read-only)
if ! chown -R "$DEV_UID" state logs 2>/dev/null; then
  echo "ERROR: chown state/ logs/ to UID $DEV_UID failed."
  echo "       Please re-run with sudo:  sudo $0"
  exit 1
fi

echo "Directories ready: config/ state/ logs/ workspace/ claude/  (DEV_UID=$DEV_UID)"
