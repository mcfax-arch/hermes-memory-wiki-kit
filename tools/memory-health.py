#!/usr/bin/env python3
"""
Формирует расширенный health-report для memory-wiki.

Это gbrain-inspired слой контроля качества: он не исправляет страницы сам,
а показывает, где память теряет связность, provenance, свежесть или recall.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, datetime
from pathlib import Path


DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)
LOCAL_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md(?:#[^)]+)?)\)")
STATUS_RE = re.compile(r"^status:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
LAST_UPDATED_RE = re.compile(r"^last_updated:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
PROVENANCE_RE = re.compile(r"^##\s+(Provenance|Источники|Провенанс)\s*$", re.IGNORECASE | re.MULTILINE)


def rel(path: Path, root: Path) -> str:
    """Возвращает portable relative path."""
    return str(path.relative_to(root)).replace("\\", "/")


def first_heading(text: str, fallback: str) -> str:
    """Извлекает первый заголовок."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
    return fallback


def capture_age_days(path: Path) -> int:
    """Возраст capture по дате в имени файла."""
    try:
        capture_date = datetime.strptime(path.name[:10], "%Y-%m-%d").date()
        return (date.today() - capture_date).days
    except ValueError:
        return 999


def iter_wiki_pages(wiki_root: Path) -> list[Path]:
    """Возвращает wiki pages без captures/index/backups."""
    pages = []
    for path in sorted(wiki_root.rglob("*.md")):
        parts = path.relative_to(wiki_root).parts
        if "captures" in parts or path.name == "index.md" or path.name.endswith(".bak"):
            continue
        pages.append(path)
    return pages


def resolve_link(source_path: Path, wiki_root: Path, target: str) -> str:
    """Резолвит локальную markdown-ссылку относительно страницы."""
    clean = target.split("#", 1)[0].replace("\\", "/")
    try:
        target_path = (source_path.parent / clean).resolve()
        return rel(target_path, wiki_root.resolve())
    except Exception:
        return clean


def build_health(memory_root: Path) -> dict:
    """Собирает health metrics."""
    wiki_root = memory_root / "knowledge-base" / "wiki"
    raw_inbox = memory_root / "knowledge-base" / "raw" / "inbox"
    pages = iter_wiki_pages(wiki_root)
    page_set = {rel(path, wiki_root): path for path in pages}

    missing_provenance = []
    missing_status = []
    missing_last_updated = []
    duplicate_titles: dict[str, list[str]] = {}
    broken_links = []
    outbound_count: dict[str, int] = {page: 0 for page in page_set}
    inbound_count: dict[str, int] = {page: 0 for page in page_set}

    for path in pages:
        page_rel = rel(path, wiki_root)
        text = path.read_text(encoding="utf-8", errors="replace")
        title = first_heading(text, path.stem).strip()
        duplicate_titles.setdefault(title.lower(), []).append(page_rel)

        if not PROVENANCE_RE.search(text):
            missing_provenance.append(page_rel)
        if not STATUS_RE.search(text):
            missing_status.append(page_rel)
        if not LAST_UPDATED_RE.search(text):
            missing_last_updated.append(page_rel)

        for match in LOCAL_LINK_RE.finditer(text):
            target_rel = resolve_link(path, wiki_root, match.group(2))
            if target_rel in page_set:
                outbound_count[page_rel] += 1
                inbound_count[target_rel] += 1
            elif not match.group(2).startswith(("http", "mailto:")):
                broken_links.append({
                    "page": page_rel,
                    "target": match.group(2),
                    "label": match.group(1),
                })

    duplicate_title_pages = {
        title: paths
        for title, paths in duplicate_titles.items()
        if title and len(paths) > 1
    }

    captures_dir = wiki_root / "captures"
    captures = sorted(captures_dir.glob("*.md")) if captures_dir.exists() else []
    promotable_captures = [rel(path, wiki_root) for path in captures if capture_age_days(path) >= 7]
    stale_captures = [rel(path, wiki_root) for path in captures if capture_age_days(path) > 30]
    raw_inbox_files = sorted(str(path.relative_to(raw_inbox)).replace("\\", "/") for path in raw_inbox.rglob("*") if path.is_file()) if raw_inbox.exists() else []

    zero_inbound = sorted(page for page, count in inbound_count.items() if count == 0)
    zero_outbound = sorted(page for page, count in outbound_count.items() if count == 0)

    issues = {
        "missing_provenance": missing_provenance,
        "missing_status": missing_status,
        "missing_last_updated": missing_last_updated,
        "broken_links": broken_links,
        "duplicate_titles": duplicate_title_pages,
        "zero_inbound": zero_inbound,
        "zero_outbound": zero_outbound,
        "promotable_captures": promotable_captures,
        "stale_captures": stale_captures,
        "raw_inbox_files": raw_inbox_files,
    }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "memory_root": str(memory_root),
        "stats": {
            "pages": len(pages),
            "captures": len(captures),
            "raw_inbox_files": len(raw_inbox_files),
            "issues_total": sum(len(value) if not isinstance(value, dict) else len(value) for value in issues.values()),
        },
        "issues": issues,
    }


def write_report(health: dict, report_path: Path) -> None:
    """Пишет markdown health-report."""
    issues = health["issues"]
    rows = [
        ("Missing provenance", len(issues["missing_provenance"])),
        ("Missing status", len(issues["missing_status"])),
        ("Missing last_updated", len(issues["missing_last_updated"])),
        ("Broken links", len(issues["broken_links"])),
        ("Duplicate titles", len(issues["duplicate_titles"])),
        ("Zero inbound", len(issues["zero_inbound"])),
        ("Zero outbound", len(issues["zero_outbound"])),
        ("Promotable captures", len(issues["promotable_captures"])),
        ("Stale captures", len(issues["stale_captures"])),
        ("Raw inbox files", len(issues["raw_inbox_files"])),
    ]

    lines = [
        "# Memory Health Report",
        "",
        "status: active",
        f"last_updated: {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        f"- Pages: {health['stats']['pages']}",
        f"- Captures: {health['stats']['captures']}",
        f"- Raw inbox files: {health['stats']['raw_inbox_files']}",
        "",
        "| Dimension | Issues |",
        "|---|---:|",
    ]
    lines.extend(f"| {name} | {count} |" for name, count in rows)

    lines.extend(["", "## Details", ""])
    for name, key in [
        ("Missing provenance", "missing_provenance"),
        ("Missing status", "missing_status"),
        ("Missing last_updated", "missing_last_updated"),
        ("Zero inbound", "zero_inbound"),
        ("Zero outbound", "zero_outbound"),
        ("Promotable captures", "promotable_captures"),
        ("Stale captures", "stale_captures"),
        ("Raw inbox files", "raw_inbox_files"),
    ]:
        values = issues[key]
        lines.extend([f"### {name}", ""])
        if values:
            lines.extend(f"- `{value}`" for value in values[:30])
            if len(values) > 30:
                lines.append(f"- ... and {len(values) - 30} more")
        else:
            lines.append("- none")
        lines.append("")

    lines.extend(["### Broken links", ""])
    if issues["broken_links"]:
        for item in issues["broken_links"][:30]:
            lines.append(f"- `{item['page']}` -> `{item['target']}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.extend(["### Duplicate titles", ""])
    if issues["duplicate_titles"]:
        for title, paths in list(issues["duplicate_titles"].items())[:30]:
            lines.append(f"- `{title}`: {', '.join(paths)}")
    else:
        lines.append("- none")

    lines.extend([
        "",
        "## Provenance",
        "",
        "- Generated by `memory-health.py` from `knowledge-base/wiki` and `knowledge-base/raw/inbox`.",
        "",
    ])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate memory-wiki health report")
    parser.add_argument("--memory-root", default=str(DEFAULT_MEMORY_ROOT), help="Корень памяти")
    parser.add_argument("--json", action="store_true", help="Печатать JSON вместо краткого текста")
    parser.add_argument("--dry-run", action="store_true", help="Не записывать report/json")
    args = parser.parse_args()

    print("=== Memory health ===")
    memory_root = Path(args.memory_root)
    health = build_health(memory_root)
    report_path = memory_root / "knowledge-base" / "state" / "reports" / f"memory-health-{date.today().isoformat()}.md"
    json_path = memory_root / "knowledge-base" / "state" / "memory_health.json"

    if args.json:
        print(json.dumps(health, ensure_ascii=False, indent=2))
    else:
        print(f"[*] Pages: {health['stats']['pages']}")
        print(f"[*] Captures: {health['stats']['captures']}")
        print(f"[*] Issues total: {health['stats']['issues_total']}")

    if args.dry_run:
        print(f"[DRY-RUN] Report would be written: {report_path}")
        print(f"[DRY-RUN] JSON would be written: {json_path}")
    else:
        write_report(health, report_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] Report written: {report_path}")
        print(f"[OK] JSON written: {json_path}")

    print("=== Готово ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

