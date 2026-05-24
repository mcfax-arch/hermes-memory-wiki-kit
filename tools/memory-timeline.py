#!/usr/bin/env python3
"""
Строит timeline.jsonl из log.md и captures.

Timeline нужен для дешёвых ответов на вопросы "когда мы это делали" без
перечитывания всей wiki.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path


DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)
LOG_HEADING_RE = re.compile(r"^##\s+\[(\d{4}-\d{2}-\d{2})\]\s+([^|]+)\|\s+(.+)$")


def capture_title(path: Path) -> str:
    """Извлекает ## Что из capture."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## что":
            for value in lines[idx + 1:]:
                if value.strip() and not value.startswith("## "):
                    return value.strip()
                if value.startswith("## "):
                    break
    return path.stem


def capture_project(path: Path) -> str:
    """Извлекает project из frontmatter capture."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("project:"):
            return line.split(":", 1)[1].strip()
    return "general"


def build_timeline(memory_root: Path) -> list[dict]:
    """Собирает timeline entries."""
    wiki_root = memory_root / "knowledge-base" / "wiki"
    entries = []

    log_path = wiki_root / "log.md"
    if log_path.exists():
        current = None
        buffer = []
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = LOG_HEADING_RE.match(line)
            if match:
                if current:
                    current["summary"] = "\n".join(buffer).strip()
                    entries.append(current)
                current = {
                    "date": match.group(1),
                    "type": "log",
                    "category": match.group(2).strip(),
                    "title": match.group(3).strip(),
                    "source": "wiki/log.md",
                }
                buffer = []
            elif current:
                buffer.append(line)
        if current:
            current["summary"] = "\n".join(buffer).strip()
            entries.append(current)

    captures_dir = wiki_root / "captures"
    if captures_dir.exists():
        for path in sorted(captures_dir.glob("*.md")):
            date_part = path.name[:10]
            try:
                datetime.strptime(date_part, "%Y-%m-%d")
            except ValueError:
                date_part = ""
            entries.append({
                "date": date_part,
                "type": "capture",
                "category": capture_project(path),
                "title": capture_title(path),
                "source": f"wiki/captures/{path.name}",
            })

    return sorted(entries, key=lambda item: (item.get("date", ""), item.get("source", "")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build memory timeline JSONL")
    parser.add_argument("--memory-root", default=str(DEFAULT_MEMORY_ROOT), help="Корень памяти")
    parser.add_argument("--dry-run", action="store_true", help="Не писать timeline")
    args = parser.parse_args()

    print("=== Memory timeline ===")
    memory_root = Path(args.memory_root)
    entries = build_timeline(memory_root)
    target = memory_root / "knowledge-base" / "state" / "timeline.jsonl"
    print(f"[*] Entries: {len(entries)}")

    if args.dry_run:
        print(f"[DRY-RUN] Timeline would be written: {target}")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
        print(f"[OK] Timeline written: {target}")

    print("=== Готово ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

