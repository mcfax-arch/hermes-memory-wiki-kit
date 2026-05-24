#!/usr/bin/env python3
"""
Обслуживание memory-wiki: промоушен captures, реиндекс, ротация лога.

Usage:
    python memory-maintain.py --promote     # промоутит стабильные captures в wiki
    python memory-maintain.py --reindex     # регенерирует index.md из структуры директорий
    python memory-maintain.py --rotate      # ротирует log.md по месяцам
    python memory-maintain.py --all         # безопасное обслуживание без rotate
    python memory-maintain.py --full-all    # всё, включая rotate
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)
MEMORY_ROOT = DEFAULT_MEMORY_ROOT
WIKI_ROOT = MEMORY_ROOT / "knowledge-base" / "wiki"
CAPTURES_DIR = WIKI_ROOT / "captures"
LOG_FILE = WIKI_ROOT / "log.md"
INDEX_FILE = WIKI_ROOT / "index.md"
STATE_DIR = MEMORY_ROOT / "knowledge-base" / "state"
DRY_RUN = False

# Директории wiki и их отображение в секции index
DIR_SECTIONS = {
    "concepts": "## Concepts",
    "projects": "## Projects",
    "sources": "## Source Summaries",
    "decisions": "## Decisions",
    "maintenance": "## Maintenance",
    "principles": "## Principles",
    "tools": "## Tools",
}

# Минимальный возраст capture (дней) для промоушена
PROMOTE_MIN_AGE_DAYS = 7
# Минимальное количество ссылок для промоушена (учитывая улучшенную систему подсчёта)
PROMOTE_MIN_REFS = 1.5  # Эквивалентно 1-2強ким связям в старой системе


def configure_paths(memory_root: Path, dry_run: bool) -> None:
    """Настраивает глобальные пути для выбранного корня памяти."""
    global MEMORY_ROOT, WIKI_ROOT, CAPTURES_DIR, LOG_FILE, INDEX_FILE, STATE_DIR, DRY_RUN
    MEMORY_ROOT = memory_root
    WIKI_ROOT = MEMORY_ROOT / "knowledge-base" / "wiki"
    CAPTURES_DIR = WIKI_ROOT / "captures"
    LOG_FILE = WIKI_ROOT / "log.md"
    INDEX_FILE = WIKI_ROOT / "index.md"
    STATE_DIR = MEMORY_ROOT / "knowledge-base" / "state"
    DRY_RUN = dry_run


def read_frontmatter(text: str) -> dict[str, str]:
    """Парсинг YAML frontmatter из markdown."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end < 0:
        return {}
    fm_text = text[3:end].strip()
    result = {}
    for line in fm_text.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip()
    return result


def first_heading(text: str) -> str:
    """Извлечение первого заголовка H1/H2 из markdown."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
    return ""


def extract_capture_fields(text: str, fallback_title: str) -> dict[str, str]:
    """Извлекает содержимое стандартных секций capture."""
    fields = {"what": "", "why": "", "context": "", "details": ""}
    buffers = {key: [] for key in fields}
    heading_map = {
        "что": "what",
        "почему важно": "why",
        "контекст": "context",
        "details": "details",
        "детали": "details",
    }
    current_key = ""

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_key = heading_map.get(stripped[3:].strip().lower(), "")
            continue
        if current_key and not stripped.startswith("---") and not stripped.startswith(("date:", "type:", "project:")):
            buffers[current_key].append(line)

    for key, values in buffers.items():
        fields[key] = "\n".join(values).strip()

    if not fields["what"]:
        fields["what"] = fallback_title.replace("-", " ").strip().title()
    return fields


def backup_path_for(path: Path) -> Path:
    """Создаёт timestamped backup path рядом с файлом."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.stem}.{stamp}{path.suffix}.bak")


def escape_link_label(text: str) -> str:
    """Экранирует квадратные скобки в markdown link label."""
    return text.replace("[", "\\[").replace("]", "\\]")


# ─── PROMOTE ───────────────────────────────────────────────────────


def list_captures() -> list[Path]:
    """Список всех capture-файлов."""
    if not CAPTURES_DIR.exists():
        return []
    return sorted(CAPTURES_DIR.glob("*.md"))


def capture_age_days(path: Path) -> int:
    """Возраст capture в днях (по дате в имени файла)."""
    name = path.stem
    date_part = name[:10]  # YYYY-MM-DD
    try:
        capture_date = datetime.strptime(date_part, "%Y-%m-%d").date()
        return (date.today() - capture_date).days
    except ValueError:
        return 999


def count_capture_refs(capture_path: Path, all_captures: list[Path]) -> int:
    """Подсчёт ссылок на данный capture из других captures и wiki-страниц."""
    text = capture_path.read_text(encoding="utf-8")
    fm = read_frontmatter(text)
    project = fm.get("project", "general")
    
    # Извлекаем ключевые фразы из capture
    what_line = ""
    why_line = ""
    for line in text.splitlines():
        if line.strip() and not line.startswith("#") and not line.startswith("---") and not line.startswith("date:") and not line.startswith("type:") and not line.startswith("project:"):
            if not what_line:
                what_line = line.strip()
            elif not why_line and line.strip() and len(line.strip()) > 10:
                why_line = line.strip()
                break
    
    # Комбинируем что и почему для лучшего контекста
    context_text = f"{what_line} {why_line}".strip()
    
    ref_count = 0
    
    # 1. Проверяем ссылки из других captures
    for other in all_captures:
        if other == capture_path:
            continue
        other_text = other.read_text(encoding="utf-8")
        other_fm = read_frontmatter(other_text)
        if other_fm.get("project") == project:
            # Улучшенная эвристика: TF-подобный учет важных слов
            if _calculate_semantic_similarity(context_text, other_text) >= 0.3:
                ref_count += 1
    
    # 2. Проверяем ссылки из уже промоученных wiki-страниц
    ref_count += _count_wiki_references(capture_path, project, context_text)
    
    return ref_count


def _calculate_semantic_similarity(text1: str, text2: str) -> float:
    """Вычисляет семантическую схожесть между двумя текстами на основе ключевых терминов."""
    # Извлекаем значимые слова (технические термины, имена, числа)
    def extract_key_terms(text: str) -> set[str]:
        # Слова длиной >=3, включая технические термины с дефисами и точками
        # Поддерживаем латиницу и кириллицу
        words = re.findall(r'\b[a-zA-Z\u0400-\u04FF0-9][a-zA-Z\u0400-\u04FF0-9._-]{2,}\b', text.lower())
        # Фильтруем общие стоп-слова
        stop_words = {'это', 'как', 'так', 'вот', 'быть', 'на', 'не', 'что', 'который', 'все', 'еще', 'ещё', 'уже', 'уже'}
        return {w for w in words if w not in stop_words and len(w) >= 3}
    
    terms1 = extract_key_terms(text1)
    terms2 = extract_key_terms(text2)
    
    if not terms1 or not terms2:
        return 0.0
    
    # Jaccard similarity с учетом редкости терминов (упрощенный TF-IDF)
    intersection = terms1 & terms2
    union = terms1 | terms2
    
    if not union:
        return 0.0
    
    # Базовая Jaccard similarity
    jaccard = len(intersection) / len(union)
    
    # Бонус за технические термины и имена собственные
    tech_bonus = 0.0
    for term in intersection:
        if (any(c.isdigit() for c in term) or  # содержит цифры
            '_' in term or                    # содержит подчеркивание
            '.' in term or                    # содержит точку
            len(term) > 8):                   # длинные термины
            tech_bonus += 0.1
    
    return min(jaccard + tech_bonus, 1.0)


def _count_wiki_references(capture_path: Path, project: str, context_text: str) -> int:
    """Подсчёт ссылок на capture из уже промоученных wiki-страниц."""
    target_dir = WIKI_ROOT / ("projects" if project != "general" else "concepts")
    
    if not target_dir.exists():
        return 0
    
    ref_count = 0
    for wiki_file in target_dir.glob("*.md"):
        try:
            wiki_text = wiki_file.read_text(encoding="utf-8")
            if _calculate_semantic_similarity(context_text, wiki_text) >= 0.25:
                ref_count += 1
        except Exception:
            # Пропускаем файлы с ошибками чтения
            continue
    
    return ref_count


def promote_capture(capture_path: Path, wiki_root: Path) -> str | None:
    """Промоушен capture в полноценную wiki-страницу. Возвращает путь или None."""
    text = capture_path.read_text(encoding="utf-8")
    fm = read_frontmatter(text)
    project = fm.get("project", "general")

    # Определяем целевую директорию
    if project != "general":
        target_dir = wiki_root / "projects"
    else:
        # Определяем по содержимому: concepts vs sources vs generic
        target_dir = wiki_root / "concepts"

    if not DRY_RUN:
        target_dir.mkdir(parents=True, exist_ok=True)

    # Имя файла из slug capture (убираем дату в начале)
    stem = capture_path.stem
    # Формат: YYYY-MM-DD-slug → переносим дату в конец
    date_part = stem[:10]
    slug_part = stem[11:] if len(stem) > 11 else "capture"
    new_name = f"{slug_part}.md"
    target_path = target_dir / new_name

    if target_path.exists():
        return None

    fields = extract_capture_fields(text, slug_part)
    title = fields["what"].splitlines()[0].strip() if fields["what"] else slug_part.replace("-", " ").title()
    if title.lower() in {"что", "what"}:
        title = slug_part.replace("-", " ").title()

    enhanced_lines = [
        f"# {title}",
        "",
        "status: active",
        f"last_updated: {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        fields["what"],
        "",
        "## Why It Matters",
        "",
        fields["why"] or "Captured as durable memory.",
    ]
    if fields["context"]:
        enhanced_lines.extend(["", "## Context", "", fields["context"]])
    if fields["details"]:
        enhanced_lines.extend(["", "## Details", "", fields["details"]])
    enhanced_lines.extend([
        "",
        "## Provenance",
        "",
        f"- Promoted from capture `{capture_path.name}` on {date.today().isoformat()}."
    ])
    enhanced = "\n".join(enhanced_lines) + "\n"

    if DRY_RUN:
        return str(target_path)

    target_path.write_text(enhanced, encoding="utf-8")
    capture_path.unlink()
    return str(target_path)


def cmd_promote() -> int:
    """Промоушен стабильных captures в wiki-страницы."""
    print("=== Promote: captures -> wiki ===")

    captures = list_captures()
    if not captures:
        print("[*] Captures не найдены.")
        print("=== Готово ===")
        return 0

    print(f"[*] Найдено captures: {len(captures)}")

    # Сначала определяем кандидатов, потом промоутим — чтобы не читать удалённые файлы
    candidates = []
    skipped = 0
    for cap in captures:
        age = capture_age_days(cap)
        refs = count_capture_refs(cap, captures)
        if age >= PROMOTE_MIN_AGE_DAYS or refs >= PROMOTE_MIN_REFS:
            candidates.append(cap)
        else:
            skipped += 1

    promoted = 0
    for cap in candidates:
        result = promote_capture(cap, WIKI_ROOT)
        if result:
            prefix = "[DRY-RUN]" if DRY_RUN else "[OK]"
            print(f" {prefix} {cap.name} -> {result}")
            promoted += 1
        else:
            skipped += 1

    label = "Будет продвинуто" if DRY_RUN else "Продвинуто"
    print(f"\n  {label} : {promoted}")
    print(f"  Пропущено  : {skipped}")
    print("=== Готово ===")
    return 0


# ─── REINDEX ───────────────────────────────────────────────────────


def scan_wiki_pages(wiki_root: Path) -> dict[str, list[tuple[str, str]]]:
    """Сканирование wiki-директорий. Возвращает {секция: [(имя_файла, описание)]}."""
    sections: dict[str, list[tuple[str, str]]] = {}

    for dir_name, section_header in DIR_SECTIONS.items():
        dir_path = wiki_root / dir_name
        if not dir_path.exists():
            continue
        pages = []
        for md_file in sorted(dir_path.glob("*.md")):
            text = md_file.read_text(encoding="utf-8")
            heading = first_heading(text)
            if not heading:
                fm = read_frontmatter(text)
                heading = fm.get("title", md_file.stem.replace("-", " ").title())
            rel_path = f"{dir_name}/{md_file.name}"
            pages.append((rel_path, heading))
        if pages:
            sections[dir_name] = pages

    return sections


def generate_index(wiki_root: Path) -> str:
    """Генерация содержимого index.md."""
    today = date.today().isoformat()
    lines = [
        "# Knowledge Base Index",
        "",
        f"status: active",
        f"last_updated: {today}",
        "",
        "## Core Pages",
        "",
        "- [Log](log.md) - chronological activity log.",
        "- [Codex memory system](principles/codex-memory-system.md) - design principles.",
        "- [Projects map](projects-map.md) - entry point for project-specific memories.",
        "- [Contradictions](contradictions.md) - claims that conflict or need reconciliation.",
        "- [Open questions](open_questions.md) - unresolved questions worth revisiting.",
        "- [Glossary](glossary.md) - recurring terms and local vocabulary.",
        "",
    ]

    # Captures
    captures = list_captures()
    lines.append(f"## Captures ({len(captures)} pending)")
    lines.append("")
    if captures:
        for cap in captures[-10:]:
            text = cap.read_text(encoding="utf-8")
            fm = read_frontmatter(text)
            project = fm.get("project", "general")
            what = ""
            for ln in text.splitlines():
                if ln.strip() and not ln.startswith("#") and not ln.startswith("---") and not ln.startswith("date:") and not ln.startswith("type:") and not ln.startswith("project:"):
                    what = ln.strip()[:80]
                    break
            lines.append(f"- [{escape_link_label(cap.name)}](captures/{cap.name}) - {project}: {what}")
        if len(captures) > 10:
            lines.append(f"- ... and {len(captures) - 10} more")
    else:
        lines.append("- No pending captures.")
    lines.append("")

    # Секции из директорий
    sections = scan_wiki_pages(wiki_root)
    for dir_name, section_header in DIR_SECTIONS.items():
        if dir_name not in sections:
            continue
        lines.append(section_header)
        lines.append("")
        for rel_path, heading in sections[dir_name]:
            lines.append(f"- [{escape_link_label(heading)}]({rel_path})")
        lines.append("")



    lines.extend([
        "## Provenance",
        "",
        f"- Auto-generated by `memory-maintain.py --reindex` on {today}.",
    ])

    return "\n".join(lines) + "\n"


def cmd_reindex() -> int:
    """Регенерация index.md из файловой структуры."""
    print("=== Reindex: регенерация index.md ===")

    content = generate_index(WIKI_ROOT)

    # Бэкап текущего index
    if INDEX_FILE.exists():
        backup = backup_path_for(INDEX_FILE)
        if DRY_RUN:
            print(f"[DRY-RUN] Бэкап будет создан: {backup}")
        else:
            INDEX_FILE.replace(backup)
            print(f"[*] Бэкап: {backup}")

    if DRY_RUN:
        print(f"[DRY-RUN] index.md будет обновлён ({len(content.splitlines())} строк)")
    else:
        INDEX_FILE.write_text(content, encoding="utf-8")
        print(f"[OK] index.md обновлён ({len(content.splitlines())} строк)")
    print("=== Готово ===")
    return 0


# ─── ROTATE ────────────────────────────────────────────────────────


def parse_log_entries(text: str) -> list[tuple[str, str]]:
    """Парсинг log.md на записи. Возвращает [(дата_строка, текст_записи)]."""
    entries = []
    # Паттерн: ## [YYYY-MM-DD] ... или ## [YYYY-MM-DD]
    current_date = ""
    current_text_lines: list[str] = []

    header_pattern = re.compile(r"^##\s+\[(\d{4}-\d{2}-\d{2})\]")

    for line in text.splitlines():
        match = header_pattern.match(line)
        if match:
            if current_date:
                entries.append((current_date, "\n".join(current_text_lines).strip()))
            current_date = match.group(1)
            current_text_lines = [line]
        elif current_date:
            current_text_lines.append(line)

    if current_date:
        entries.append((current_date, "\n".join(current_text_lines).strip()))

    return entries


def month_key(date_str: str) -> str:
    """Ключ месяца из строки даты YYYY-MM-DD."""
    return date_str[:7]  # YYYY-MM


def cmd_rotate() -> int:
    """Ротация log.md: архивация записей старше текущего месяца."""
    print("=== Rotate: ротация log.md ===")

    if not LOG_FILE.exists():
        print("[*] log.md не найден.")
        print("=== Готово ===")
        return 0

    text = LOG_FILE.read_text(encoding="utf-8")
    entries = parse_log_entries(text)

    if not entries:
        print("[*] log.md пуст или формат не распознан.")
        print("=== Готово ===")
        return 0

    current_month = date.today().strftime("%Y-%m")
    archive_months: dict[str, list[tuple[str, str]]] = {}
    current_entries: list[tuple[str, str]] = []

    for date_str, entry_text in entries:
        mk = month_key(date_str)
        if mk == current_month:
            current_entries.append((date_str, entry_text))
        else:
            if mk not in archive_months:
                archive_months[mk] = []
            archive_months[mk].append((date_str, entry_text))

    # Записываем архивные файлы
    archived_count = 0
    for mk, month_entries in sorted(archive_months.items()):
        archive_path = WIKI_ROOT / f"log-{mk}.md"
        if archive_path.exists():
            # Дописываем в существующий архив
            existing = archive_path.read_text(encoding="utf-8")
            new_lines = [e[1] for e in month_entries]
            if DRY_RUN:
                print(f"  [DRY-RUN] Архив будет дополнен: {archive_path.name} ({len(month_entries)} записей)")
            else:
                archive_path.write_text(existing.rstrip() + "\n\n" + "\n\n".join(new_lines) + "\n", encoding="utf-8")
        else:
            lines = [
                f"# Knowledge Base Log (archive: {mk})",
                "",
            ]
            for _, entry_text in month_entries:
                lines.append(entry_text)
                lines.append("")
            if DRY_RUN:
                print(f"  [DRY-RUN] Архив будет создан: {archive_path.name} ({len(month_entries)} записей)")
            else:
                archive_path.write_text("\n".join(lines), encoding="utf-8")
        archived_count += len(month_entries)
        if not DRY_RUN:
            print(f"  [OK] Архив: {archive_path.name} ({len(month_entries)} записей)")

    # Перезаписываем log.md только текущим месяцем
    header_lines = [
        "# Knowledge Base Log",
        "",
        "status: active",
        f"last_updated: {date.today().isoformat()}",
        "",
    ]
    for _, entry_text in current_entries:
        header_lines.append(entry_text)
        header_lines.append("")

    if DRY_RUN:
        print(f"  [DRY-RUN] log.md будет перезаписан текущим месяцем ({len(current_entries)} записей)")
    else:
        LOG_FILE.write_text("\n".join(header_lines), encoding="utf-8")

    print(f"\n  Архивировано : {archived_count} записей в {len(archive_months)} файлов")
    print(f"  Осталось     : {len(current_entries)} записей в log.md")
    print("=== Готово ===")
    return 0


# ─── STATS ────────────────────────────────────────────────────────


def cmd_stats() -> int:
    """Статистика системы памяти."""
    print("=== Stats: состояние памяти ===")

    categories: dict[str, int] = {}
    for dir_name in DIR_SECTIONS:
        dir_path = WIKI_ROOT / dir_name
        if dir_path.exists():
            count = len(list(dir_path.glob("*.md")))
            categories[dir_name] = count

    root_pages = 0
    for p in WIKI_ROOT.glob("*.md"):
        if p.name != "index.md" and not (p.name.startswith("index.") and p.name.endswith(".bak")) and "captures" not in p.parts:
            root_pages += 1
    categories["root"] = root_pages

    captures = list_captures()
    capture_ages = [capture_age_days(c) for c in captures] if captures else []

    last_maint = "never"
    if INDEX_FILE.exists():
        idx_text = INDEX_FILE.read_text(encoding="utf-8")
        for line in idx_text.splitlines():
            if line.startswith("last_updated:"):
                last_maint = line.split(":", 1)[1].strip()
                break

    total = sum(categories.values())
    print(f"[*] Всего страниц: {total} + {len(captures)} captures = {total + len(captures)}")
    print()
    print("  Категории:")
    for cat, count in sorted(categories.items()):
        print(f"    {cat}: {count}")
    print()
    print(f"  Последнее обслуживание index: {last_maint}")
    if captures:
        ages = [capture_age_days(c) for c in captures]
        print(f"  Captures: {len(captures)} шт")
        print(f"    Самый старый: {max(ages)} дн — {captures[ages.index(max(ages))].name}")
        print(f"    Самый новый : {min(ages)} дн — {captures[ages.index(min(ages))].name}")
        stale = [c for c in captures if capture_age_days(c) > 30]
        if stale:
            print(f"  [WARN] Застарелые captures (>30 дн): {len(stale)}")
            for cap in stale:
                age = capture_age_days(cap)
                print(f"    {cap.name} ({age} дн)")
    else:
        print("  Captures: 0")
    print("=== Готово ===")
    return 0


# ─── ORPHANS ──────────────────────────────────────────────────────


def cmd_orphans() -> int:
    """Поиск страниц не связанных в index.md и битых ссылок."""
    print("=== Orphans: поиск сирот и битых ссылок ===")

    if not INDEX_FILE.exists():
        print("[*] index.md не найден.")
        print("=== Готово ===")
        return 0

    all_files: set[str] = set()
    for f in WIKI_ROOT.rglob("*.md"):
        if "captures" in f.parts or f.name == "index.md" or f.name.endswith(".bak"):
            continue
        rel = f.relative_to(WIKI_ROOT)
        all_files.add(str(rel).replace("\\", "/"))

    idx_text = INDEX_FILE.read_text(encoding="utf-8")
    linked: set[str] = set()
    for m in re.finditer(r"\[((?:\\.|[^\]\\])*)\]\(([^)]+)\)", idx_text):
        target = m.group(2)
        if not target.startswith(("http", "#", "mailto:")):
            linked.add(target)

    orphans = sorted(all_files - linked)
    if orphans:
        print(f"  [WARN] Страниц без ссылки в index.md: {len(orphans)}")
        for o in orphans[:10]:
            print(f"    {o}")
        if len(orphans) > 10:
            print(f"    ... и ещё {len(orphans) - 10}")
    else:
        print("  [OK] Все страницы связаны в index.md")

    dead_links = []
    for link in linked:
        if link.startswith("captures/"):
            continue
        target_path = WIKI_ROOT / link.replace("/", "\\")
        if not target_path.exists():
            dead_links.append(link)
    if dead_links:
        print(f"  [WARN] Битых ссылок в index.md: {len(dead_links)}")
        for d in dead_links[:10]:
            print(f"    -> {d}")
    else:
        print("  [OK] Нет битых ссылок")

    print("=== Готово ===")
    return 0


# ─── REFRESH STATE ────────────────────────────────────────────────

LAST_UPDATED_RE = re.compile(r"^(last_updated:\s*).*", re.IGNORECASE | re.MULTILINE)


def cmd_refresh_state() -> int:
    """Обновление last_updated во всех state-файлах."""
    print("=== Refresh state: обновление last_updated ===")

    if not STATE_DIR.exists():
        print("[*] state/ не найден.")
        print("=== Готово ===")
        return 0

    today = date.today().isoformat()
    updated = 0
    for f in STATE_DIR.rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        new_text, n = LAST_UPDATED_RE.subn(rf"\g<1>{today}", text)
        if n:
            if DRY_RUN:
                print(f"  [DRY-RUN] {f.name} -> last_updated: {today}")
            else:
                f.write_text(new_text, encoding="utf-8")
                print(f"  [OK] {f.name} -> last_updated: {today}")
            updated += 1

    label = "Будет обновлено файлов" if DRY_RUN else "Обновлено файлов"
    print(f"\n  {label}: {updated}")
    print("=== Готово ===")
    return 0


# ─── HEALTH ────────────────────────────────────────────────────────


def cmd_health() -> int:
    """Запускает расширенный health-report."""
    print("=== Health: расширенный отчёт памяти ===")
    script = MEMORY_ROOT / "knowledge-base" / "tools" / "memory-health.py"
    command = [sys.executable, str(script), "--memory-root", str(MEMORY_ROOT)]
    if DRY_RUN:
        command.append("--dry-run")
    code = subprocess.call(command)
    print("=== Готово ===")
    return code


# ─── MAIN ──────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Обслуживание memory-wiki"
    )
    parser.add_argument("--promote", action="store_true", help="Промоутить стабильные captures в wiki")
    parser.add_argument("--reindex", action="store_true", help="Регенерировать index.md")
    parser.add_argument("--rotate", action="store_true", help="Ротировать log.md по месяцам")
    parser.add_argument("--stats", action="store_true", help="Статистика системы памяти")
    parser.add_argument("--orphans", action="store_true", help="Поиск сирот и битых ссылок")
    parser.add_argument("--refresh-state", action="store_true", help="Обновить last_updated в state/")
    parser.add_argument("--health", action="store_true", help="Расширенный health-report")
    parser.add_argument("--all", action="store_true", help="Безопасное обслуживание: promote, reindex, stats, orphans, refresh-state")
    parser.add_argument("--full-all", action="store_true", help="Полное обслуживание: --all плюс rotate")
    parser.add_argument("--dry-run", action="store_true", help="Показать изменения без записи/удаления файлов")
    parser.add_argument(
        "--memory-root",
        default=str(DEFAULT_MEMORY_ROOT),
        help="Корень памяти (default: HERMES_MEMORY_ROOT, AI_MEMORY_ROOT или ~/Hermes_Memory)",
    )
    args = parser.parse_args()

    configure_paths(Path(args.memory_root), args.dry_run)

    if not any([args.promote, args.reindex, args.rotate, args.stats, args.orphans, args.refresh_state, args.health, args.all, args.full_all]):
        parser.print_help()
        return 1

    results = {}
    safe_all = args.all or args.full_all

    if safe_all or args.promote:
        results["promote"] = cmd_promote()
    if safe_all or args.reindex:
        results["reindex"] = cmd_reindex()
    if args.full_all or args.rotate:
        results["rotate"] = cmd_rotate()
    if safe_all or args.stats:
        results["stats"] = cmd_stats()
    if safe_all or args.orphans:
        results["orphans"] = cmd_orphans()
    if safe_all or args.refresh_state:
        results["refresh_state"] = cmd_refresh_state()
    if args.health:
        results["health"] = cmd_health()

    print(f"\n=== Итого ===")
    for op, code in results.items():
        status = "OK" if code == 0 else "ERROR"
        print(f"  {op}: {status}")
    print("=== Готово ===")

    return max(results.values()) if results else 0


if __name__ == "__main__":
    raise SystemExit(main())

