#!/usr/bin/env bash
# setup-maintenance.sh — настроить ежедневное обслуживание wiki через cron
#
# Использование:
#   ./setup-maintenance.sh                           # интерактивный режим
#   ./setup-maintenance.sh --install                 # установить без запросов
#   ./setup-maintenance.sh --uninstall               # удалить cron задачу
#   ./setup-maintenance.sh --status                  # проверить статус
#   ./setup-maintenance.sh --time "06:00"            # установить время

set -euo pipefail

SCRIPT_NAME="wiki-maintenance"
SCRIPT_PATH="$(cd "$(dirname "$0")" && pwd)/wiki-maintenance.py"
CRON_FILE="/tmp/hermes-wiki-maintenance.cron"

# ── Определяем время ──────────────────────────────────────────────────
TIME="${2:-06:00}"
HOUR="${TIME%%:*}"
MINUTE="${TIME##*:}"
# если передали только час
[[ "$TIME" == *:* ]] || { MINUTE=0; HOUR="$TIME"; }
# убираем ведущие нули для cron
HOUR="${HOUR#0}"
MINUTE="${MINUTE#0}"

# ── Статус ────────────────────────────────────────────────────────────
status() {
    if crontab -l 2>/dev/null | grep -q "$SCRIPT_NAME"; then
        echo "✅ Maintenance cron active:"
        crontab -l | grep "$SCRIPT_NAME"
    else
        echo "❌ Maintenance cron NOT installed"
    fi
}

# ── Установка ─────────────────────────────────────────────────────────
install() {
    # проверяем, существует ли скрипт
    if [ ! -f "$SCRIPT_PATH" ]; then
        echo "❌ Script not found: $SCRIPT_PATH"
        echo "   Запусти из директории tools/ репозитория"
        exit 1
    fi
    chmod +x "$SCRIPT_PATH"

    # создаём временный файл с новой cron задачей + старыми
    crontab -l 2>/dev/null | grep -v "$SCRIPT_NAME" > "$CRON_FILE" || true

    # добавляем задачу
    echo "# Hermes Memory Wiki — ежедневное обслуживание в $TIME" >> "$CRON_FILE"
    echo "$MINUTE $HOUR * * * cd \"$(dirname "$SCRIPT_PATH")\" && python3 \"$SCRIPT_PATH\" >> \"$HOME/.wiki-maintenance.log\" 2>&1" >> "$CRON_FILE"

    crontab "$CRON_FILE"
    rm -f "$CRON_FILE"

    echo "✅ Установлено: ежедневное обслуживание в $TIME"
    echo "   Лог: $HOME/.wiki-maintenance.log"
    echo "   Запусти вручную: python3 \"$SCRIPT_PATH\""
}

# ── Удаление ──────────────────────────────────────────────────────────
uninstall() {
    crontab -l 2>/dev/null | grep -v "$SCRIPT_NAME" > "$CRON_FILE" || true
    crontab "$CRON_FILE"
    rm -f "$CRON_FILE"
    echo "✅ Maintenance cron удалён"
}

# ── Main ──────────────────────────────────────────────────────────────
case "${1:-}" in
    --install|-i)
        install
        ;;
    --uninstall|-u)
        uninstall
        ;;
    --status|-s)
        status
        ;;
    --time|-t)
        TIME="${2:-06:00}"
        install
        ;;
    *)
        echo "📋 Hermes Memory Wiki — Maintenance Setup"
        echo ""
        echo "Подкоманды:"
        echo "  --install            Установить ежедневный cron"
        echo "  --uninstall          Удалить cron задачу"
        echo "  --status             Проверить статус"
        echo "  --time HH:MM         Установить время выполнения"
        echo ""
        echo "Пример:"
        echo "  ./setup-maintenance.sh --time 09:00"
        echo "  ./setup-maintenance.sh --status"
        echo ""
        status
        ;;
esac
