#!/usr/bin/env python3
"""
Линтер wiki-памяти: provenance, битые ссылки, orphan pages, stale captures.

Usage:
    python lint_wiki.py [путь_к_wiki]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path


LINK_PATTERN = re.compile(r"\[((?:\\.|[^\]\\])*)\]\(([^)]+)\)")
WINDOWS_ABS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)
PROVENANCE_HEADINGS = ("## Provenance", "## Источники", "## Провенанс")


def find_markdown_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.md"))


def check_provenance_and_links(path: Path, root: Path) -> list[str]:
    """Проверка секции provenance и битых ссылок в одном файле."""
    warnings: list[str] = []
    text = path.read_text(encoding="utf-8")

    # Captures — временные файлы, provenance появляется при promote
    if "captures" not in path.parts:
        if not any(heading in text for heading in PROVENANCE_HEADINGS):
            rel = path.relative_to(root)
            warnings.append(f"  {rel}: missing provenance/source section")

    for match in LINK_PATTERN.finditer(text):
        target = match.group(2).strip()
        if "://" in target or target.startswith("#") or target.startswith("mailto:"):
            continue
        if Path(target).is_absolute() or WINDOWS_ABS_PATH.match(target):
            continue
        clean_target = target.split("#", 1)[0]
        if not clean_target:
            continue
        resolved = (path.parent / clean_target).resolve()
        if not resolved.exists():
            rel = path.relative_to(root)
            warnings.append(f"  {rel}: broken link -> {target}")

    return warnings


def check_orphans(root: Path) -> list[str]:
    """Поиск .md файлов не связанных в index.md."""
    index_path = root / "index.md"
    if not index_path.exists():
        return []

    all_files: set[str] = set()
    for f in root.rglob("*.md"):
        if f.name == "index.md" or f.name.endswith(".bak"):
            continue
        if "captures" in f.parts:
            continue
        rel = f.relative_to(root)
        all_files.add(str(rel).replace("\\", "/"))

    idx_text = index_path.read_text(encoding="utf-8")
    linked: set[str] = set()
    for m in LINK_PATTERN.finditer(idx_text):
        target = m.group(2)
        if not target.startswith(("http", "#", "mailto:")):
            linked.add(target)

    orphans = sorted(all_files - linked)
    warnings: list[str] = []
    if orphans:
        for o in orphans:
            warnings.append(f"  orphan: {o} — not linked in index.md")
    return warnings


def check_stale_captures(root: Path, max_age_days: int = 30) -> list[str]:
    """Поиск captures старше max_age_days."""
    cap_dir = root / "captures"
    if not cap_dir.exists():
        return []

    today = date.today()
    warnings: list[str] = []
    for f in sorted(cap_dir.glob("*.md")):
        name = f.stem
        date_part = name[:10]
        try:
            cap_date = date.fromisoformat(date_part)
            age = (today - cap_date).days
            if age > max_age_days:
                warnings.append(f"  stale capture: {f.name} ({age} дней)")
        except ValueError:
            warnings.append(f"  unparseable capture date: {f.name}")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Линтер wiki-памяти")
    parser.add_argument("wiki_path", nargs="?", help="Путь к wiki/. Если не задан, берётся из --memory-root")
    parser.add_argument(
        "--memory-root",
        default=str(DEFAULT_MEMORY_ROOT),
        help="Корень памяти (default: HERMES_MEMORY_ROOT, AI_MEMORY_ROOT или ~/Hermes_Memory)",
    )
    args = parser.parse_args()

    root = Path(args.wiki_path) if args.wiki_path else Path(args.memory_root) / "knowledge-base" / "wiki"

    print("=== Линтер wiki-памяти ===")
    print(f"[*] Корень: {root}")

    if not root.exists():
        print(f"[ERROR] Папка не найдена: {root}")
        return 1

    files = find_markdown_files(root)
    warnings: list[str] = []

    for path in files:
        warnings.extend(check_provenance_and_links(path, root))

    warnings.extend(check_orphans(root))
    warnings.extend(check_stale_captures(root))

    print()
    print("=== Итого ===")
    print(f"  Файлов проверено : {len(files)}")
    print(f"  Предупреждений   : {len(warnings)}")

    for w in warnings:
        print(f"[WARN] {w}")

    if warnings:
        print()
        print("Рекомендация: запусти `python memory-maintain.py --all` для полного обслуживания.")

    print("=== Готово ===")
    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())

