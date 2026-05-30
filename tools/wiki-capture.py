#!/usr/bin/env python3
"""
wiki-capture — CLI quick capture tool for Hermes Memory Wiki.

Usage:
    python wiki-capture.py "KPM service restarts automatically on boot" --tags windows service kpm
    python wiki-capture.py "Key insight: preview didn't show inner toolbar" --tags ui bug
    python wiki-capture.py --list                       # list recent captures
    python wiki-capture.py --list --unpromoted           # list captures not yet promoted
    python wiki-capture.py --list --tag windows          # filter by tag

Saves a timestamped capture that can later be promoted to a full wiki page
via `python wiki-maintenance.py --promote`.
"""

import argparse
import sys
import time
from pathlib import Path


def find_wiki_root() -> Path | None:
    candidates = [
        Path.home() / "AppData/Local/hermes/memory-wiki",
        Path.home() / ".hermes/memory-wiki",
        Path.home() / ".local/share/hermes/memory-wiki",
    ]
    for c in candidates:
        if (c / "wiki").exists():
            return c
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Quick capture for Hermes Memory Wiki")
    ap.add_argument("content", nargs="?", default="", help="Fact to remember")
    ap.add_argument("--tags", "-t", type=str, default="", help="Space or comma-separated tags")
    ap.add_argument("--wiki-path", type=str, default="", help="Wiki root path (auto-detect if omitted)")
    ap.add_argument("--list", action="store_true", help="List recent captures")
    ap.add_argument("--unpromoted", action="store_true", help="When listing, only unpromoted captures")
    ap.add_argument("--tag", type=str, default="", help="When listing, filter by tag")
    ap.add_argument("--limit", type=int, default=10, help="Max captures to list (default: 10)")

    args = ap.parse_args()

    # Find wiki root
    root = Path(args.wiki_path) if args.wiki_path else find_wiki_root()
    if not root or not (root / "wiki").exists():
        print("❌ Wiki root not found. Specify --wiki-path or check HERMES_HOME.")
        return 1

    # Import WikiStore
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins"))
    try:
        from memory_wiki.store import WikiStore
    except ImportError:
        # Fallback: try direct path
        store_path = Path(__file__).resolve().parent.parent / "plugins" / "memory_wiki" / "store.py"
        if store_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("store", str(store_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            WikiStore = mod.WikiStore
        else:
            print("❌ Cannot import WikiStore. Run from Hermes environment.")
            return 1

    store = WikiStore(root)

    if args.list:
        captures = store.list_captures(
            limit=args.limit,
            tag_filter=args.tag,
            only_unpromoted=args.unpromoted,
        )
        if not captures:
            print("📭 No captures found.")
            return 0

        print(f"📋 Captures ({len(captures)} shown):")
        print()
        for c in captures:
            tag_str = f" [{', '.join(c['tags'])}]" if c['tags'] else ""
            promoted = " ✅" if c['promoted'] else ""
            print(f"  {c['id']}{promoted}{tag_str}")
            print(f"    {c['content'][:120]}")
            print(f"    {c['created_str']}")
            print()
        return 0

    if not args.content:
        print("❌ Provide content to capture or use --list to view existing captures.")
        return 1

    result = store.add_capture(content=args.content, tags=args.tags, source="cli")
    if "error" in result:
        print(f"❌ {result['error']}")
        return 1

    print(f"✅ Captured: {result['id']}")
    print(f"   Content: {args.content[:100]}")
    if result['tags']:
        print(f"   Tags: {', '.join(result['tags'])}")
    print(f"   Path: {result['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
