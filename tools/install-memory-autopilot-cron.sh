#!/usr/bin/env sh
set -eu

MEMORY_ROOT="${HERMES_MEMORY_ROOT:-${AI_MEMORY_ROOT:-$HOME/Hermes_Memory}}"
TIME_SPEC="30 3 * * *"

usage() {
  cat <<'EOF'
Usage:
  ./install-memory-autopilot-cron.sh [--memory-root PATH] [--time "30 3 * * *"]

Installs a user crontab entry for deterministic memory autopilot.
Default schedule is daily at 03:30.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --memory-root)
      MEMORY_ROOT="$2"
      shift 2
      ;;
    --time)
      TIME_SPEC="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SCRIPT="$MEMORY_ROOT/knowledge-base/tools/memory-autopilot.py"
LOG="$MEMORY_ROOT/knowledge-base/state/reports/autopilot-cron.log"
MARKER="# HermesMemoryAutopilot"
LINE="$TIME_SPEC HERMES_MEMORY_ROOT=\"$MEMORY_ROOT\" /usr/bin/env python3 \"$SCRIPT\" --memory-root \"$MEMORY_ROOT\" >> \"$LOG\" 2>&1 $MARKER"

mkdir -p "$MEMORY_ROOT/knowledge-base/state/reports"

tmp=$(mktemp)
(crontab -l 2>/dev/null || true) | grep -v "$MARKER" > "$tmp"
printf '%s\n' "$LINE" >> "$tmp"
crontab "$tmp"
rm -f "$tmp"

echo "[OK] Installed cron autopilot:"
echo "$LINE"
