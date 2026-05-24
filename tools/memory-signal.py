#!/usr/bin/env python3
"""
Always-on capture gate для memory-wiki.

Скрипт получает текст сообщения/наблюдения, решает, достоин ли он capture,
редактирует очевидные секреты и вызывает memory-write.py. Это не LLM:
он дешёвый, локальный и предназначен для частого запуска агентом.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b[A-Za-z0-9_]{20,}\.[A-Za-z0-9_=-]{20,}\.[A-Za-z0-9_=-]{20,}\b"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
]
SIGNAL_TERMS = {
    "decision": ["решили", "решение", "оставляем", "приняли", "не будем", "decision"],
    "preference": ["я хочу", "мне важно", "предпочитаю", "давай всегда", "по умолчанию"],
    "bug": ["сломалось", "ошибка", "не работает", "root cause", "фикс", "исправь"],
    "architecture": ["архитектура", "механизм", "инвариант", "workflow", "protocol", "система"],
    "tooling": ["настрой", "подключи", "mcp", "opencode", "codex", "скрипт", "команда", "автоматизировать"],
    "memory": ["память", "memory", "capture", "always-on", "autopilot", "cron", "токены", "лимиты"],
    "project_state": ["следующий шаг", "что дальше", "заблокировано", "open loop", "план"],
    "source": ["проанализируй", "репозиторий", "paper", "статья", "источник", "github"],
}


def load_text(args: argparse.Namespace) -> str:
    """Загружает текст из аргумента или файла."""
    if args.text:
        return args.text
    if args.message_file:
        return Path(args.message_file).read_text(encoding="utf-8", errors="replace")
    return sys.stdin.read()


def redact(text: str) -> tuple[str, bool]:
    """Редактирует очевидные секреты."""
    changed = False
    result = text
    for pattern in SECRET_PATTERNS:
        new_result = pattern.sub("[REDACTED_SECRET]", result)
        changed = changed or new_result != result
        result = new_result
    return result, changed


def classify(text: str) -> tuple[int, list[str]]:
    """Оценивает силу сигнала и категории."""
    lower = text.lower()
    categories = []
    score = 0
    if len(text.strip()) >= 180:
        score += 1
    if "\n" in text.strip():
        score += 1
    for category, terms in SIGNAL_TERMS.items():
        if any(term in lower for term in terms):
            categories.append(category)
            score += 2
    if re.search(r"[A-Za-z]:[\\/]|(?:^|\s)(?:/|~/|\./|\.\./)|\.py\b|\.md\b|\.json\b|\.toml\b", text):
        categories.append("path_or_file")
        score += 1
    return score, sorted(set(categories))


def summarize(text: str, categories: list[str]) -> str:
    """Делает короткий заголовок capture без LLM."""
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "Durable memory signal")
    first_line = re.sub(r"\s+", " ", first_line)
    if len(first_line) > 140:
        first_line = first_line[:137].rstrip() + "..."
    if categories:
        return f"{first_line} [{', '.join(categories[:3])}]"
    return first_line


def run_memory_write(memory_root: Path, what: str, project: str, why: str, context: str, details: str, dry_run: bool) -> int:
    """Вызывает memory-write.py с безопасной передачей аргументов."""
    script = memory_root / "knowledge-base" / "tools" / "memory-write.py"
    command = [
        sys.executable,
        str(script),
        what,
        "--project",
        project,
        "--why",
        why,
        "--context",
        context,
        "--details",
        details,
        "--memory-root",
        str(memory_root),
    ]
    if dry_run:
        command.append("--dry-run")
    return subprocess.call(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect durable memory signal and create capture")
    parser.add_argument("--text", default="", help="Текст сообщения")
    parser.add_argument("--message-file", default="", help="Файл с текстом сообщения")
    parser.add_argument("--project", default="general", help="Проект capture")
    parser.add_argument("--source", default="conversation", help="Источник/контекст capture")
    parser.add_argument("--min-score", type=int, default=2, help="Минимальный score для capture")
    parser.add_argument("--force", action="store_true", help="Записать capture независимо от score")
    parser.add_argument("--dry-run", action="store_true", help="Показать решение без записи")
    parser.add_argument("--memory-root", default=str(DEFAULT_MEMORY_ROOT), help="Корень памяти")
    args = parser.parse_args()

    print("=== Memory signal ===")
    text = load_text(args).strip()
    if not text:
        print("[SKIP] Empty text")
        print("=== Готово ===")
        return 0

    redacted_text, had_secret = redact(text)
    score, categories = classify(redacted_text)
    print(f"[*] Score: {score}")
    print(f"[*] Categories: {', '.join(categories) if categories else 'none'}")

    if had_secret and not args.force:
        print("[SKIP] Secret-like text detected; redacted preview only. Use --force only after manual review.")
        print("=== Готово ===")
        return 0

    if not args.force and score < args.min_score:
        print("[SKIP] Signal below threshold")
        print("=== Готово ===")
        return 0

    what = summarize(redacted_text, categories)
    why = f"Always-on capture signal: {', '.join(categories) if categories else 'manual force'}"
    context = args.source
    details = redacted_text if len(redacted_text) <= 2400 else redacted_text[:2400].rstrip() + "\n...[truncated]"

    print(f"[*] Capture title: {what}")
    code = run_memory_write(Path(args.memory_root), what, args.project, why, context, details, args.dry_run)
    print("=== Готово ===")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

