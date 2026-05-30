#!/usr/bin/env python3
"""
Wiki Maintenance — full health, capture promotion, decay pass, link graph report.

Usage:
    python wiki-maintenance.py                      # full maintenance (decay + promote)
    python wiki-maintenance.py --decay              # tier decay pass only
    python wiki-maintenance.py --promote            # promote ready captures to wiki pages
    python wiki-maintenance.py --health             # health report only
    python wiki-maintenance.py --graph              # link graph report
    python wiki-maintenance.py --dry-run            # show what would change without applying
    python wiki-maintenance.py --verbose            # full output

Run daily via cron for automated wiki upkeep.
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
        if (c / "wiki").exists() and (c / "index.md").exists():
            return c
    return None


def load_store(root: Path):
    """Load WikiStore from the Hermes plugin directory."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "plugins"))
    try:
        from memory_wiki.store import WikiStore
        return WikiStore(root)
    except ImportError:
        store_path = Path(__file__).resolve().parent.parent / "plugins" / "memory_wiki" / "store.py"
        if store_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("store", str(store_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.WikiStore(root)
        raise


# ── Decay pass ──────────────────────────────────────────────────────────

FRONTMATTER_RE = __import__('re').compile(r"^---\s*\n(.*?)\n---\s*\n?", __import__('re').DOTALL)
WARM_AFTER = 30
STALE_AFTER = 90


def load_frontmatter(content: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(content)
    if not m:
        return {}, content
    try:
        import yaml
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    body = content[m.end():]
    return meta, body


def render_frontmatter(meta: dict) -> str:
    if not meta:
        return ""
    try:
        import yaml
        lines = yaml.dump(meta, default_flow_style=False, allow_unicode=True).strip()
    except ImportError:
        lines = "\n".join(f"{k}: {v}" for k, v in meta.items()
                         if not isinstance(v, (dict, list)))
    return f"---\n{lines}\n---\n"


def run_decay(wiki_dir: Path, dry_run: bool, verbose: bool) -> int:
    """Update tier based on mtime."""
    now = time.time()
    changed = 0
    errors = 0

    for f in sorted(wiki_dir.glob("*.md")):
        if f.name in ("index.md", "log.md") or f.parent.name == "_captures":
            continue
        try:
            mtime = f.stat().st_mtime
            age_days = (now - mtime) / 86400

            if age_days > STALE_AFTER:
                target_tier = "stale"
            elif age_days > WARM_AFTER:
                target_tier = "warm"
            else:
                target_tier = "active"

            content = f.read_text(encoding="utf-8")
            meta, body = load_frontmatter(content)
            current_tier = meta.get("tier", "active")

            if current_tier == target_tier:
                continue

            meta["tier"] = target_tier
            front = render_frontmatter(meta) if meta else ""
            new_content = front + body

            if not dry_run:
                f.write_text(new_content, encoding="utf-8")

            line = f"   {f.stem:30s} {current_tier:>8s} → {target_tier:<8s}  (age={age_days:.1f}d)"
            if verbose:
                print(line)
            else:
                print(line)
            changed += 1
        except Exception as e:
            print(f"   ⚠️  {f.name}: {e}")
            errors += 1

    if verbose or changed > 0:
        print(f"\n📊 Decay: {changed} pages updated, {errors} errors")
    return changed


# ── Capture promotion ───────────────────────────────────────────────────

def run_promote(store, wiki_dir: Path, dry_run: bool, verbose: bool) -> int:
    """Auto-promote ripe captures to full wiki pages.

    Promotion rules:
    - Captures with 2+ occurrences of same tag → merge into topic page
    - Captures with 80+ chars → candidate for promotion
    - Captures older than 7 days still unpromoted → flag for review
    """
    if verbose:
        print("\n🔍 Scanning captures for promotion candidates...")

    captures = store.list_captures(limit=200, only_unpromoted=True)
    if not captures:
        if verbose:
            print("   No unpromoted captures found.")
        return 0

    # Group by tags
    tag_groups: dict[str, list] = {}
    untagged = []

    for c in captures:
        if c["tags"]:
            for t in c["tags"]:
                if t not in tag_groups:
                    tag_groups[t] = []
                tag_groups[t].append(c)
        else:
            untagged.append(c)

    # Find tag groups with 2+ captures — suggest merge
    promoted = 0
    now = time.time()

    for tag, tagged_captures in sorted(tag_groups.items()):
        if len(tagged_captures) < 2:
            continue

        target_page = f"captures/{tag}"
        if dry_run:
            print(f"   Would promote {len(tagged_captures)} captures to wiki/{target_page}")
            continue

        # Check if page already exists
        existing = store.read_page(target_page)
        if existing:
            body = existing["body"]
        else:
            body = f"# {tag.title()}\n\n_Auto-generated from captures._\n\n"

        body += f"\n## Batch promotion — {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        for c in tagged_captures:
            body += f"- {c['content']}  \n"
            store.promote_capture(c["id"], target_page)
            promoted += 1

        store.write_raw_page(target_page, body)
        store._log_entry(
            action="promote_batch",
            target=target_page,
            message=f"Promoted {len(tagged_captures)} captures via tag '{tag}'",
        )

        if verbose:
            print(f"   ✅ {target_page}: {len(tagged_captures)} captures promoted")

    # Flag old unpromoted captures
    old_captures = [c for c in captures if (now - c["created"]) / 86400 > 7 and not c["promoted"]]
    if old_captures and verbose:
        print(f"\n   ⏳ {len(old_captures)} captures older than 7 days still unpromoted:")
        for c in old_captures[:5]:
            print(f"      {c['id']}: {c['content'][:80]}")

    if verbose and not promoted:
        print("   No captures met promotion criteria.")

    return promoted


# ── Health report ───────────────────────────────────────────────────────

def run_health(store, root: Path, verbose: bool) -> None:
    health = store.get_health()
    graph_stats = health.get("link_graph", {})

    print(f"\n{'═' * 50}")
    print(f"📊 WIKI HEALTH REPORT")
    print(f"{'═' * 50}")
    print(f"\n📍 Location: {root}")
    print(f"\n📄 Pages:      {health['page_count']}  ({health['total_size_kb']} KB)")

    tiers = health.get("tiers", {})
    print(f"\n🏷️  Tiers:")
    print(f"   Active: {tiers.get('active', 0)}")
    print(f"   Warm:   {tiers.get('warm', 0)}")
    print(f"   Stale:  {tiers.get('stale', 0)}")
    print(f"   Avg age: {health.get('avg_age_days', 'N/A')}d  |  Oldest: {health.get('oldest_days', 'N/A')}d")

    print(f"\n🔗 Link Graph:")
    print(f"   Total links:    {graph_stats.get('total_links', 0)}")
    print(f"   Avg outgoing:   {graph_stats.get('avg_outgoing_per_page', 0)} per page")
    print(f"   Pages w/ links: {graph_stats.get('pages_with_links', 0)}")

    orphans = health.get("orphans", [])
    if orphans:
        print(f"\n👻 Orphan pages ({len(orphans)}):")
        for o in orphans[:10]:
            print(f"   - {o['name']} ({o['title']})")
        if len(orphans) > 10:
            print(f"   ... and {len(orphans) - 10} more")

    broken = health.get("broken_links", [])
    if broken:
        print(f"\n💔 Broken [[links]] ({len(broken)}):")
        for b in broken[:10]:
            sources = ", ".join(b["source_pages"][:3])
            print(f"   - [[{b['target']}]] (refs: {b['referenced_by']}, from: {sources})")
        if len(broken) > 10:
            print(f"   ... and {len(broken) - 10} more")

    captures = health.get("unpromoted_captures", 0)
    print(f"\n📥 Captures:")
    print(f"   Unpromoted: {captures}")

    score = health.get("health_score", 100)
    bar = "█" * int(score / 10) + "░" * (10 - int(score / 10))
    print(f"\n💚 Health Score: {score}% [{bar}]")

    if verbose:
        print(f"\n  {'─' * 40}")
        print(f"  Components:")
        if captures > 10:
            print(f"    ⚠️  {captures} unpromoted captures (backlog)")
        stale_pct = tiers.get("stale", 0) / max(health["page_count"], 1)
        if stale_pct > 0.3:
            print(f"    ⚠️  {stale_pct * 100:.0f}% pages stale")
        if len(broken) > 0:
            print(f"    ⚠️  {len(broken)} broken links")
        if len(orphans) > health["page_count"] * 0.5:
            print(f"    💡 Most pages are orphans — add [[wiki-links]] to connect them")

    print(f"\n{'═' * 50}\n")


# ── Link graph report ──────────────────────────────────────────────────

def run_graph(store, verbose: bool) -> None:
    pages = store.list_pages(sort="mtime")
    if not pages:
        print("📭 No pages in wiki.")
        return

    print(f"\n{'═' * 50}")
    print(f"🔗 LINK GRAPH REPORT")
    print(f"{'═' * 50}")

    # Most connected pages
    page_connections = []
    for p in pages:
        graph = store.get_graph(p["name"])
        total = graph["outgoing_count"] + graph["incoming_count"]
        if total > 0:
            page_connections.append((p["name"], p["title"], total, graph))

    page_connections.sort(key=lambda x: -x[2])

    if page_connections:
        print(f"\n🌐 Most connected pages:")
        for name, title, total, graph in page_connections[:10]:
            out = graph["outgoing_count"]
            inn = graph["incoming_count"]
            print(f"   {name:30s}  {total:3d} connections  (→{out}  ←{inn})")

    # Isolation report
    orphans = store._find_orphans()
    if orphans:
        print(f"\n👻 Orphan pages ({len(orphans)}):")
        for o in orphans[:10]:
            print(f"   - {o['name']} ({o['title']})")
        if len(orphans) > 10:
            print(f"   ... and {len(orphans) - 10} more")
        print(f"\n   💡 Add [[wiki-links]] to connect orphans to related pages.")

    # Broken links
    broken = store._find_broken_links()
    if broken:
        print(f"\n💔 Broken [[links]] ({len(broken)}):")
        for b in broken[:10]:
            sources = ", ".join(b["source_pages"][:3])
            print(f"   - [[{b['target']}]] — {b['referenced_by']} ref(s) from {sources}")

    print(f"\n{'═' * 50}\n")


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Wiki maintenance — health, decay, promote, graph")
    ap.add_argument("--wiki-path", type=str, default="", help="Wiki root path (auto-detect if omitted)")
    ap.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    ap.add_argument("--verbose", action="store_true", help="Full output")
    ap.add_argument("--decay", action="store_true", help="Tier decay pass only")
    ap.add_argument("--promote", action="store_true", help="Capture promotion only")
    ap.add_argument("--health", action="store_true", help="Health report only")
    ap.add_argument("--graph", action="store_true", help="Link graph report only")

    args = ap.parse_args()

    # Find wiki root
    root = Path(args.wiki_path) if args.wiki_path else find_wiki_root()
    if not root or not (root / "wiki").exists():
        print("❌ Wiki root not found. Specify --wiki-path")
        return 1

    wiki_dir = root / "wiki"

    # Determine mode
    mode_decay = args.decay or not (args.promote or args.health or args.graph)
    mode_promote = args.promote or not (args.decay or args.health or args.graph)
    mode_health = args.health
    mode_graph = args.graph

    store = load_store(root)

    # Health report (standalone)
    if mode_health:
        run_health(store, root, args.verbose)
        return 0

    # Graph report (standalone)
    if mode_graph:
        run_graph(store, args.verbose)
        return 0

    # Decay pass
    if mode_decay:
        if args.verbose:
            print(f"\n{'═' * 50}")
            print(f"🔧 Decay pass (dry-run={args.dry_run})")
            print(f"{'═' * 50}")
        run_decay(wiki_dir, dry_run=args.dry_run, verbose=args.verbose)

    # Capture promotion
    if mode_promote and not args.dry_run:
        run_promote(store, wiki_dir, dry_run=False, verbose=args.verbose)

    # Summary health after maintenance
    if not args.health and not args.graph:
        health = store.get_health()
        print(f"\n✅ Maintenance complete. Health: {health['health_score']}%")
        if health.get("unpromoted_captures", 0) > 0:
            print(f"   📥 {health['unpromoted_captures']} unpromoted captures remain")
        if health.get("broken_links"):
            print(f"   💔 {len(health['broken_links'])} broken links")
        if health.get("orphans"):
            print(f"   👻 {len(health['orphans'])} orphan pages")

    return 0


if __name__ == "__main__":
    sys.exit(main())
