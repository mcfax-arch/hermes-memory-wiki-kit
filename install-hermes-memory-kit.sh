#!/usr/bin/env sh
set -eu

MEMORY_ROOT="${HERMES_MEMORY_ROOT:-${AI_MEMORY_ROOT:-$HOME/Hermes_Memory}}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
INSTALL_SKILL=0
APPEND_MEMORY_BOOTSTRAP=0

usage() {
  cat <<'EOF'
Usage:
  ./install-hermes-memory-kit.sh [--memory-root PATH] [--hermes-home PATH] [--install-skill] [--append-memory-bootstrap]

Defaults:
  memory root: $HERMES_MEMORY_ROOT, $AI_MEMORY_ROOT, or ~/Hermes_Memory
  Hermes home: $HERMES_HOME or ~/.hermes
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --memory-root)
      MEMORY_ROOT="$2"
      shift 2
      ;;
    --hermes-home)
      HERMES_HOME="$2"
      shift 2
      ;;
    --install-skill)
      INSTALL_SKILL=1
      shift
      ;;
    --append-memory-bootstrap)
      APPEND_MEMORY_BOOTSTRAP=1
      shift
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

KIT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLS_SOURCE="$KIT_ROOT/tools"
TEMPLATES="$KIT_ROOT/templates"

echo "=== Hermes Memory Wiki Kit install ==="
echo "Kit root   : $KIT_ROOT"
echo "Memory root: $MEMORY_ROOT"
echo "Hermes home: $HERMES_HOME"

mkdir -p \
  "$MEMORY_ROOT" \
  "$MEMORY_ROOT/knowledge-base/raw/inbox" \
  "$MEMORY_ROOT/knowledge-base/raw/sources" \
  "$MEMORY_ROOT/knowledge-base/wiki/captures" \
  "$MEMORY_ROOT/knowledge-base/wiki/concepts" \
  "$MEMORY_ROOT/knowledge-base/wiki/projects" \
  "$MEMORY_ROOT/knowledge-base/wiki/sources" \
  "$MEMORY_ROOT/knowledge-base/wiki/decisions" \
  "$MEMORY_ROOT/knowledge-base/wiki/maintenance" \
  "$MEMORY_ROOT/knowledge-base/wiki/principles" \
  "$MEMORY_ROOT/knowledge-base/wiki/tools" \
  "$MEMORY_ROOT/knowledge-base/state/reports" \
  "$MEMORY_ROOT/knowledge-base/state/projects" \
  "$MEMORY_ROOT/knowledge-base/evals" \
  "$MEMORY_ROOT/knowledge-base/tools"

cp -R "$TOOLS_SOURCE"/. "$MEMORY_ROOT/knowledge-base/tools/"

replace_template() {
  src="$1"
  dst="$2"
  if [ ! -e "$dst" ]; then
    sed "s|{MEMORY_ROOT}|$MEMORY_ROOT|g" "$src" > "$dst"
  fi
}

replace_template "$KIT_ROOT/AGENTS.md" "$MEMORY_ROOT/AGENTS.md"
replace_template "$KIT_ROOT/HERMES.md" "$MEMORY_ROOT/HERMES.md"
replace_template "$TEMPLATES/wiki-index.md" "$MEMORY_ROOT/knowledge-base/wiki/index.md"
replace_template "$TEMPLATES/wiki-log.md" "$MEMORY_ROOT/knowledge-base/wiki/log.md"
replace_template "$TEMPLATES/projects-map.md" "$MEMORY_ROOT/knowledge-base/wiki/projects-map.md"
replace_template "$TEMPLATES/open_questions.md" "$MEMORY_ROOT/knowledge-base/wiki/open_questions.md"
replace_template "$TEMPLATES/contradictions.md" "$MEMORY_ROOT/knowledge-base/wiki/contradictions.md"

if [ "$INSTALL_SKILL" -eq 1 ]; then
  mkdir -p "$HERMES_HOME/skills/memory-wiki"
  sed "s|{MEMORY_ROOT}|$MEMORY_ROOT|g" "$KIT_ROOT/skills/memory-wiki/SKILL.md" > "$HERMES_HOME/skills/memory-wiki/SKILL.md"
  echo "[OK] Skill installed: $HERMES_HOME/skills/memory-wiki"
fi

if [ "$APPEND_MEMORY_BOOTSTRAP" -eq 1 ]; then
  mkdir -p "$HERMES_HOME/memories"
  mem_file="$HERMES_HOME/memories/MEMORY.md"
  tmp_file="$MEMORY_ROOT/knowledge-base/state/memory-bootstrap.tmp"
  sed "s|{MEMORY_ROOT}|$MEMORY_ROOT|g" "$TEMPLATES/MEMORY_BOOTSTRAP.md" > "$tmp_file"
  if [ ! -e "$mem_file" ] || ! grep -q "External long-term memory lives at" "$mem_file"; then
    if [ -s "$mem_file" ]; then
      {
        printf '\n---\n'
        cat "$tmp_file"
      } >> "$mem_file"
    else
      cp "$tmp_file" "$mem_file"
    fi
    echo "[OK] MEMORY.md bootstrap appended: $mem_file"
  else
    echo "[SKIP] MEMORY.md already has external memory bootstrap"
  fi
  rm -f "$tmp_file"
fi

echo ""
echo "Next steps:"
echo "1. Add templates/MEMORY_BOOTSTRAP.md to $HERMES_HOME/memories/MEMORY.md if not using --append-memory-bootstrap."
echo "2. Copy skills/memory-wiki to $HERMES_HOME/skills/memory-wiki if not using --install-skill."
echo "3. Optional: paste mcp/config.yaml.snippet under $HERMES_HOME/config.yaml."
echo "4. Test:"
echo "   python3 \"$MEMORY_ROOT/knowledge-base/tools/memory-maintain.py\" --stats --memory-root \"$MEMORY_ROOT\""
echo "=== Done ==="
