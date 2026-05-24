#!/usr/bin/env sh
set -eu

MARKER="# HermesMemoryAutopilot"
tmp=$(mktemp)
(crontab -l 2>/dev/null || true) | grep -v "$MARKER" > "$tmp"
crontab "$tmp"
rm -f "$tmp"
echo "[OK] Removed cron entries marked $MARKER"
