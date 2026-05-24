#!/usr/bin/env python3
"""
Строит лёгкий typed graph поверх Markdown wiki.

Скрипт не заменяет Markdown как source of truth. Он читает страницы из
knowledge-base/wiki, извлекает локальные markdown-ссылки и сохраняет
машиночитаемый граф в knowledge-base/state/link_graph.json.
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
LOCAL_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md(?:#[^)]+)?)\)")
STATUS_RE = re.compile(r"^status:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
LAST_UPDATED_RE = re.compile(r"^last_updated:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def normalize_rel(path: Path, root: Path) -> str:
    """Возвращает portable path для JSON-графа."""
    return str(path.relative_to(root)).replace("\\", "/")


def first_heading(text: str, fallback: str) -> str:
    """Извлекает первый H1/H2 из markdown."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
    return fallback


def category_for(rel_path: str) -> str:
    """Определяет категорию страницы по первой директории."""
    parts = rel_path.split("/")
    return parts[0] if len(parts) > 1 else "root"


def infer_edge_type(source_text: str, source_rel: str, target_rel: str, line: str) -> str:
    """Определяет тип связи по пути и контекстной строке."""
    lower_line = line.lower()
    target_category = category_for(target_rel)
    source_category = category_for(source_rel)

    if "provenance" in lower_line or "источник" in lower_line or target_category == "sources":
        return "source"
    if target_category == "decisions":
        return "decision_for"
    if source_category == "decisions":
        return "decision_context"
    if "contradict" in lower_line or "противореч" in lower_line:
        return "contradicts"
    if "supersed" in lower_line or "замен" in lower_line:
        return "supersedes"
    if "depends" in lower_line or "завис" in lower_line:
        return "depends_on"
    return "mentions"


def page_status(text: str) -> str:
    """Извлекает status из страницы."""
    match = STATUS_RE.search(text)
    return match.group(1).strip() if match else ""


def page_last_updated(text: str) -> str:
    """Извлекает last_updated из страницы."""
    match = LAST_UPDATED_RE.search(text)
    return match.group(1).strip() if match else ""


def iter_pages(wiki_root: Path, include_captures: bool) -> list[Path]:
    """Собирает markdown-страницы wiki."""
    pages = []
    for path in sorted(wiki_root.rglob("*.md")):
        rel_parts = path.relative_to(wiki_root).parts
        if path.name.endswith(".bak"):
            continue
        if path.name == "index.md":
            continue
        if not include_captures and "captures" in rel_parts:
            continue
        pages.append(path)
    return pages


def build_graph(memory_root: Path, include_captures: bool) -> dict:
    """Строит граф страниц и typed edges."""
    wiki_root = memory_root / "knowledge-base" / "wiki"
    pages = iter_pages(wiki_root, include_captures)
    known = {normalize_rel(path, wiki_root): path for path in pages}

    nodes = []
    edges = []
    inbound: dict[str, int] = {rel: 0 for rel in known}
    outbound: dict[str, int] = {rel: 0 for rel in known}

    for path in pages:
        rel = normalize_rel(path, wiki_root)
        text = path.read_text(encoding="utf-8", errors="replace")
        nodes.append({
            "id": rel,
            "title": first_heading(text, path.stem),
            "category": category_for(rel),
            "status": page_status(text),
            "last_updated": page_last_updated(text),
        })

        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in LOCAL_LINK_RE.finditer(line):
                target = match.group(2).split("#", 1)[0].replace("\\", "/")
                target_path = (path.parent / target).resolve() if not target.startswith(("/", "\\")) else Path(target)
                try:
                    target_rel = normalize_rel(target_path, wiki_root.resolve())
                except Exception:
                    target_rel = target

                if target_rel not in known:
                    continue

                edge_type = infer_edge_type(text, rel, target_rel, line)
                edges.append({
                    "from": rel,
                    "to": target_rel,
                    "type": edge_type,
                    "label": match.group(1),
                    "line": line_no,
                })
                inbound[target_rel] += 1
                outbound[rel] += 1

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "memory_root": str(memory_root),
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "zero_inbound": sum(1 for count in inbound.values() if count == 0),
            "zero_outbound": sum(1 for count in outbound.values() if count == 0),
            "edge_types": sorted({edge["type"] for edge in edges}),
        },
    }


def write_report(graph: dict, report_path: Path) -> None:
    """Пишет человекочитаемый отчёт по графу."""
    inbound: dict[str, int] = {node["id"]: 0 for node in graph["nodes"]}
    outbound: dict[str, int] = {node["id"]: 0 for node in graph["nodes"]}
    for edge in graph["edges"]:
        outbound[edge["from"]] = outbound.get(edge["from"], 0) + 1
        inbound[edge["to"]] = inbound.get(edge["to"], 0) + 1

    weak = [
        node["id"]
        for node in graph["nodes"]
        if inbound.get(node["id"], 0) == 0 or outbound.get(node["id"], 0) == 0
    ]

    lines = [
        "# Memory Link Graph Report",
        "",
        "status: active",
        f"last_updated: {datetime.now().date().isoformat()}",
        "",
        "## Summary",
        "",
        f"- Nodes: {graph['stats']['nodes']}",
        f"- Edges: {graph['stats']['edges']}",
        f"- Zero inbound: {graph['stats']['zero_inbound']}",
        f"- Zero outbound: {graph['stats']['zero_outbound']}",
        f"- Edge types: {', '.join(graph['stats']['edge_types']) or 'none'}",
        "",
        "## Weakly Connected Pages",
        "",
    ]
    if weak:
        lines.extend(f"- `{page}`" for page in weak[:50])
        if len(weak) > 50:
            lines.append(f"- ... and {len(weak) - 50} more")
    else:
        lines.append("- none")
    lines.extend([
        "",
        "## Provenance",
        "",
        "- Generated by `memory-graph.py` from Markdown links in `knowledge-base/wiki`.",
        "",
    ])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build typed link graph for memory-wiki")
    parser.add_argument("--memory-root", default=str(DEFAULT_MEMORY_ROOT), help="Корень памяти")
    parser.add_argument("--include-captures", action="store_true", help="Включить captures в граф")
    parser.add_argument("--dry-run", action="store_true", help="Показать итог без записи файлов")
    args = parser.parse_args()

    print("=== Memory graph ===")
    memory_root = Path(args.memory_root)
    graph = build_graph(memory_root, args.include_captures)
    graph_path = memory_root / "knowledge-base" / "state" / "link_graph.json"
    report_path = memory_root / "knowledge-base" / "state" / "reports" / "memory-link-graph.md"

    print(f"[*] Nodes: {graph['stats']['nodes']}")
    print(f"[*] Edges: {graph['stats']['edges']}")
    print(f"[*] Zero inbound: {graph['stats']['zero_inbound']}")
    print(f"[*] Zero outbound: {graph['stats']['zero_outbound']}")

    if args.dry_run:
        print(f"[DRY-RUN] Graph would be written: {graph_path}")
        print(f"[DRY-RUN] Report would be written: {report_path}")
    else:
        graph_path.parent.mkdir(parents=True, exist_ok=True)
        graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
        write_report(graph, report_path)
        print(f"[OK] Graph written: {graph_path}")
        print(f"[OK] Report written: {report_path}")

    print("=== Готово ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

