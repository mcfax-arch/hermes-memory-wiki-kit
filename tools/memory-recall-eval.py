#!/usr/bin/env python3
"""
Простой replay-eval для проверки recall memory-wiki.

Файл cases хранит реальные вопросы и ожидаемые страницы. Скрипт использует
детерминированный lexical retrieval поверх Markdown, чтобы быстро замечать,
когда память перестала находить важные страницы.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path


DEFAULT_MEMORY_ROOT = Path(
    os.environ.get(
        "HERMES_MEMORY_ROOT",
        os.environ.get("AI_MEMORY_ROOT", str(Path.home() / "Hermes_Memory")),
    )
)
TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9._-]{3,}")
STOP_WORDS = {
    "что", "как", "это", "для", "или", "нам", "уже", "про", "при", "без",
    "the", "and", "for", "with", "from", "this", "that",
}


def tokenize(text: str) -> list[str]:
    """Токенизирует запрос или страницу."""
    return [token.lower() for token in TOKEN_RE.findall(text) if token.lower() not in STOP_WORDS]


def first_heading(text: str, fallback: str) -> str:
    """Извлекает заголовок страницы."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped.startswith("## "):
            return stripped[3:].strip()
    return fallback


def load_pages(memory_root: Path) -> list[dict]:
    """Загружает страницы wiki в компактный индекс."""
    wiki_root = memory_root / "knowledge-base" / "wiki"
    pages = []
    for path in sorted(wiki_root.rglob("*.md")):
        rel_parts = path.relative_to(wiki_root).parts
        if "captures" in rel_parts or path.name.endswith(".bak"):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.relative_to(wiki_root)).replace("\\", "/")
        title = first_heading(text, path.stem)
        tokens = tokenize(title + "\n" + text)
        pages.append({"path": rel, "title": title, "tokens": tokens, "text": text})
    return pages


def score_page(query_tokens: list[str], page: dict) -> float:
    """Считает простой lexical score."""
    if not query_tokens:
        return 0.0
    token_counts: dict[str, int] = {}
    for token in page["tokens"]:
        token_counts[token] = token_counts.get(token, 0) + 1

    score = 0.0
    title_tokens = set(tokenize(page["title"]))
    for token in query_tokens:
        if token in token_counts:
            score += 1.0 + min(token_counts[token], 5) * 0.15
        if token in title_tokens:
            score += 2.0
    return score


def retrieve(query: str, pages: list[dict], top_k: int) -> list[dict]:
    """Возвращает top_k страниц."""
    query_tokens = tokenize(query)
    scored = [
        {"path": page["path"], "title": page["title"], "score": score_page(query_tokens, page)}
        for page in pages
    ]
    return [item for item in sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k] if item["score"] > 0]


def sample_cases() -> list[dict]:
    """Возвращает стартовые eval-cases."""
    return [
        {
            "query": "Where is the memory index?",
            "expected_pages": ["index.md"],
            "notes": "Core index recall",
        },
        {
            "query": "Where is the memory activity log?",
            "expected_pages": ["log.md"],
            "notes": "Log recall",
        },
        {
            "query": "Where are project entry points tracked?",
            "expected_pages": ["projects-map.md"],
            "notes": "Projects map recall",
        },
    ]


def ensure_sample_cases(cases_path: Path) -> None:
    """Создаёт стартовые eval-cases, если их ещё нет."""
    if cases_path.exists():
        return
    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases = sample_cases()
    with cases_path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")


def load_cases(cases_path: Path) -> list[dict]:
    """Читает JSONL cases."""
    cases = []
    if not cases_path.exists():
        return cases
    for line_no, line in enumerate(cases_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            cases.append(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Bad JSONL at {cases_path}:{line_no}: {exc}") from exc
    return cases


def run_eval(memory_root: Path, top_k: int, dry_run: bool) -> dict:
    """Запускает recall eval."""
    cases_path = memory_root / "knowledge-base" / "evals" / "memory-recall-cases.jsonl"
    if cases_path.exists():
        cases = load_cases(cases_path)
    elif dry_run:
        cases = sample_cases()
    else:
        ensure_sample_cases(cases_path)
        cases = load_cases(cases_path)
    pages = load_pages(memory_root)

    results = []
    passed = 0
    for case in cases:
        hits = retrieve(case["query"], pages, top_k)
        hit_paths = [hit["path"] for hit in hits]
        expected = case.get("expected_pages", [])
        ok = any(path in hit_paths for path in expected)
        if ok:
            passed += 1
        results.append({
            "query": case["query"],
            "expected_pages": expected,
            "hits": hits,
            "passed": ok,
        })

    return {
        "date": date.today().isoformat(),
        "cases_path": str(cases_path),
        "top_k": top_k,
        "passed": passed,
        "total": len(cases),
        "results": results,
    }


def write_report(result: dict, report_path: Path) -> None:
    """Пишет Markdown отчёт eval."""
    lines = [
        "# Memory Recall Eval Report",
        "",
        "status: active",
        f"last_updated: {date.today().isoformat()}",
        "",
        "## Summary",
        "",
        f"- Passed: {result['passed']} / {result['total']}",
        f"- Top K: {result['top_k']}",
        "",
        "## Cases",
        "",
    ]
    for item in result["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        lines.extend([
            f"### {status}: {item['query']}",
            "",
            "Expected:",
        ])
        lines.extend(f"- `{path}`" for path in item["expected_pages"])
        lines.append("")
        lines.append("Hits:")
        lines.extend(f"- `{hit['path']}` ({hit['score']:.2f})" for hit in item["hits"][:10])
        lines.append("")
    lines.extend([
        "## Provenance",
        "",
        f"- Cases: `{result['cases_path']}`",
        "- Generated by `memory-recall-eval.py`.",
        "",
    ])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run memory-wiki recall replay eval")
    parser.add_argument("--memory-root", default=str(DEFAULT_MEMORY_ROOT), help="Корень памяти")
    parser.add_argument("--top-k", type=int, default=5, help="Сколько результатов считать successful window")
    parser.add_argument("--dry-run", action="store_true", help="Не писать отчёт")
    args = parser.parse_args()

    print("=== Memory recall eval ===")
    memory_root = Path(args.memory_root)
    result = run_eval(memory_root, args.top_k, args.dry_run)
    print(f"[*] Passed: {result['passed']} / {result['total']}")

    for item in result["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"  [{status}] {item['query']}")
        for hit in item["hits"][:3]:
            print(f"       {hit['path']} ({hit['score']:.2f})")

    report_path = memory_root / "knowledge-base" / "state" / "reports" / f"memory-recall-eval-{date.today().isoformat()}.md"
    if args.dry_run:
        print(f"[DRY-RUN] Report would be written: {report_path}")
    else:
        write_report(result, report_path)
        print(f"[OK] Report written: {report_path}")

    print("=== Готово ===")
    return 0 if result["passed"] == result["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

