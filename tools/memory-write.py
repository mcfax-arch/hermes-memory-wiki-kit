#!/usr/bin/env python3
"""
Быстрый захват знаний в memory-wiki.
Создаёт compact capture-файл в captures/ без обновления index/log.

Usage:
    python memory-write.py "Что сделано" [--project exo] [--why "Почему важно"] [--context "file.py:42"]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path


DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)

# Маппинг русских символов для slug
_SLUG_RU = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str, max_words: int = 5, max_len: int = 60) -> str:
    """Транслитерация и очистка текста в slug."""
    text = text.lower().strip()
    # Транслитерация русских
    result = []
    for ch in text:
        if ch in _SLUG_RU:
            result.append(_SLUG_RU[ch])
        elif ch in " -_":
            result.append("-")
        elif ch.isalnum():
            result.append(ch)
    slug = "".join(result)
    # Убираем дубликаты дефисов
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    # Ограничиваем длину
    words = slug.split("-")
    slug = "-".join(words[:max_words])
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "capture"


def build_capture(what: str, project: str, why: str, context: str, details: str = "") -> str:
    """Формирование capture-файла."""
    today = date.today().isoformat()
    lines = [
        "---",
        f"date: {today}",
        "type: capture",
        f"project: {project}",
        "---",
        f"## Что",
        what,
        f"## Почему важно",
        why if why else "see context",
    ]
    if context:
        lines.extend(["## Контекст", context])
    if details:
        lines.extend(["## Details", details])
    return "\n".join(lines) + "\n"


def captures_dir(memory_root: Path) -> Path:
    """Возвращает путь к captures/ для выбранного корня памяти."""
    return memory_root / "knowledge-base" / "wiki" / "captures"


def write_capture(what: str, project: str, why: str, context: str, memory_root: Path, dry_run: bool = False, details: str = "") -> Path:
    """Запись capture-файла на диск."""
    target_captures_dir = captures_dir(memory_root)
    if not dry_run:
        target_captures_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    slug = slugify(what)
    filename = f"{today}-{slug}.md"
    filepath = target_captures_dir / filename

    # Если файл с таким именем уже есть — добавляем счётчик
    counter = 1
    while filepath.exists():
        filepath = target_captures_dir / f"{today}-{slug}-{counter}.md"
        counter += 1

    content = build_capture(what, project, why, context, details)
    if not dry_run:
        filepath.write_text(content, encoding="utf-8")
    return filepath


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Быстрый захват знаний в memory-wiki captures/"
    )
    parser.add_argument("what", help="Что сделано/решено (1 строка)")
    parser.add_argument(
        "--project", default="general", help="Проект (default: general)"
    )
    parser.add_argument("--why", default="", help="Почему это знание ценно")
    parser.add_argument("--context", default="", help="file:line или ссылка")
    parser.add_argument("--details", default="", help="Расширенный фрагмент или детали для будущего промоушена")
    parser.add_argument(
        "--memory-root",
        default=str(DEFAULT_MEMORY_ROOT),
        help="Корень памяти (default: HERMES_MEMORY_ROOT, AI_MEMORY_ROOT или ~/Hermes_Memory)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Показать путь capture без записи файла")
    args = parser.parse_args()

    print("=== memory-write: захват знания ===")

    memory_root = Path(args.memory_root)
    filepath = write_capture(args.what, args.project, args.why, args.context, memory_root, args.dry_run, args.details)

    status = "[DRY-RUN]" if args.dry_run else "[OK]"
    action = "Capture будет записан" if args.dry_run else "Capture записан"
    print(f"{status} {action}: {filepath}")
    print(f"     Корень : {memory_root}")
    print(f"     Проект : {args.project}")
    print(f"     Что    : {args.what[:80]}")
    print("=== Готово ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

