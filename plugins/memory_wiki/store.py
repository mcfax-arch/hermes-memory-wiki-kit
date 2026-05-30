"""Memory-wiki storage engine: Markdown wiki with FTS5 search, link graph, captures.

Stores memory as human-readable Markdown files in a wiki directory.
Uses Python's built-in sqlite3 with FTS5 for full-text search.
No external dependencies -- pure stdlib.

Features:
- FTS5 full-text search with recency/tier/boost scoring
- Link graph: [[wiki-links]] auto-tracked between pages
- Captures: quick fact capture without full page creation
- Health reporting: tiers, orphans, graph stats, capture backlog
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_SEE_ALSO_RE = re.compile(r"^##?\s+See\s+also\s*$", re.MULTILINE | re.IGNORECASE)
_MAX_LOG_ENTRIES = 500


class WikiStore:
    """Markdown wiki storage engine.

    Directory layout:
      {root}/wiki/              -- Markdown pages (*.md)
      {root}/wiki/_captures/    -- Quick capture files (*.{ts}.md)
      {root}/index.md           -- Auto-generated table of contents
      {root}/log.md             -- Chronological log of memory writes
      {root}/.state/db          -- SQLite FTS5 search index + link graph + captures

    Thread-safe: all public methods use a per-instance RLock.
    """

    # ── Init ──────────────────────────────────────────────────────────

    def __init__(self, root: str | Path, auto_init: bool = True):
        self._root = Path(root)
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        if auto_init:
            self._ensure_dirs()
            self._open_db()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def wiki_dir(self) -> Path:
        return self._root / "wiki"

    @property
    def captures_dir(self) -> Path:
        return self._root / "wiki" / "_captures"

    @property
    def state_dir(self) -> Path:
        return self._root / ".state"

    def _ensure_dirs(self):
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _open_db(self):
        db_path = self.state_dir / "search.db"
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=OFF")
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS pages (
                path TEXT PRIMARY KEY,
                title TEXT,
                modified REAL,
                tier TEXT DEFAULT 'active',
                content TEXT
            )"""
        )
        self._db.execute(
            """CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
               USING fts5(path, title, content, tokenize='unicode61')"""
        )
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )
        # Migrate v2a: add tier column
        try:
            self._db.execute("ALTER TABLE pages ADD COLUMN tier TEXT DEFAULT 'active'")
        except sqlite3.OperationalError:
            pass

        # ── v3: link graph table ──
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS links (
                source TEXT,
                target TEXT,
                weight INTEGER DEFAULT 1,
                first_seen REAL,
                last_seen REAL,
                PRIMARY KEY (source, target)
            )"""
        )
        # v3: captures table
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS captures (
                id TEXT PRIMARY KEY,
                content TEXT,
                tags TEXT DEFAULT '',
                source TEXT DEFAULT '',
                created REAL,
                promoted INTEGER DEFAULT 0
            )"""
        )

        # v3: signals log (autopilot keeps a rolling log)
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                signal_type TEXT,
                score REAL,
                content TEXT,
                created REAL
            )"""
        )

        # Migrate v3: add source column if upgrading from v2
        try:
            self._db.execute(
                "ALTER TABLE captures ADD COLUMN source TEXT DEFAULT ''"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._db.execute(
                "ALTER TABLE captures ADD COLUMN promoted INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass

        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_captures_promoted ON captures(promoted)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_captures_created ON captures(created)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_links_target ON links(target)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type)"
        )

        self._db.commit()

    def close(self):
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    # ── Auto-indexing ─────────────────────────────────────────────────

    def index_all_existing(self) -> dict:
        """Scan the wiki directory and index all .md files not yet in FTS.

        Returns dict with counts: indexed, skipped, errors.
        """
        if not self.wiki_dir.exists():
            return {"indexed": 0, "skipped": 0, "errors": 0}

        indexed = set()
        if self._db:
            try:
                rows = self._db.execute("SELECT path FROM pages").fetchall()
                indexed = {r[0] for r in rows}
            except Exception:
                pass

        counts = {"indexed": 0, "skipped": 0, "errors": 0}

        for f in sorted(self.wiki_dir.glob("*.md")):
            if f.name in ("index.md", "log.md") or f.parent.name == "_captures":
                continue
            name = self._page_name_from_path(f)
            if name in indexed:
                counts["skipped"] += 1
                continue
            try:
                content = f.read_text(encoding="utf-8")
                title = self._extract_title(content) or name
                self._index_page(name, title, content)
                self._update_links(name, content)
                counts["indexed"] += 1
            except Exception as e:
                logger.warning("Failed to index %s: %s", f, e)
                counts["errors"] += 1

        self._rebuild_index()
        return counts

    # ── Page CRUD ─────────────────────────────────────────────────────

    def _page_path(self, name: str) -> Path:
        safe = name.replace("..", "_").replace("/", os.sep).replace("\\", "_")
        return (self.wiki_dir / safe).with_suffix(".md")

    def _page_name_from_path(self, path: Path) -> str:
        rel = path.relative_to(self.wiki_dir)
        stem = rel.with_suffix("")
        return str(stem.as_posix())

    def _extract_frontmatter(self, content: str) -> tuple[dict, str]:
        m = _FRONTMATTER_RE.match(content)
        if not m:
            return {}, content
        try:
            import yaml
            meta = yaml.safe_load(m.group(1)) or {}
        except Exception:
            meta = {}
        body = content[m.end():]
        return meta, body

    def _render_frontmatter(self, meta: dict) -> str:
        if not meta:
            return ""
        try:
            import yaml
            lines = yaml.dump(meta, default_flow_style=False, allow_unicode=True).strip()
        except ImportError:
            lines = "\n".join(f"{k}: {v}" for k, v in meta.items()
                             if not isinstance(v, (dict, list)))
        return f"---\n{lines}\n---\n"

    def _extract_title(self, content: str) -> str:
        m = _HEADING_RE.search(content)
        return m.group(1).strip() if m else ""

    def _extract_tags(self, content: str, meta: dict) -> list[str]:
        tags = []
        if "tags" in meta:
            raw = meta["tags"]
            if isinstance(raw, str):
                tags = [t.strip() for t in raw.replace(",", " ").split()]
            elif isinstance(raw, list):
                tags = [str(t) for t in raw]
        else:
            body = content.split("\n---\n")[-1] if "\n---\n" in content else content
            tags = re.findall(r"(?<!\w)#(\w[\w-]*)", body)
        return tags

    def page_exists(self, name: str) -> bool:
        return self._page_path(name).exists()

    def read_page(self, name: str) -> dict | None:
        path = self._page_path(name)
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            logger.warning("Failed to read page %s: %s", name, e)
            return None

        meta, body = self._extract_frontmatter(content)
        title = meta.get("title", self._extract_title(body) or name)
        tags = self._extract_tags(content, meta)
        modified = os.path.getmtime(path)

        return {
            "name": name,
            "title": title,
            "path": str(path),
            "content": content,
            "body": body,
            "meta": meta,
            "tags": tags,
            "modified": modified,
            "size": len(content),
        }

    def write_page(
        self,
        name: str,
        content: str,
        meta: dict | None = None,
        commit_message: str = "",
    ) -> dict:
        with self._lock:
            path = self._page_path(name)
            existed = path.exists()

            existing_meta, existing_body = self._extract_frontmatter(content)
            if existing_meta:
                if meta:
                    existing_meta.update(meta)
                final_meta = existing_meta
                final_body = existing_body
            else:
                final_meta = meta or {}
                final_body = content

            if "title" not in final_meta:
                extracted = self._extract_title(final_body)
                if extracted:
                    final_meta["title"] = extracted

            front = self._render_frontmatter(final_meta) if final_meta else ""
            final_content = front + final_body

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(final_content, encoding="utf-8")

            title = final_meta.get("title", self._extract_title(final_body) or name)
            self._index_page(name, title, final_content)
            self._update_links(name, final_content)
            self._rebuild_index()
            if commit_message:
                self._log_entry(
                    action="write" if existed else "create",
                    target=name,
                    message=commit_message,
                )
            self._trim_log_if_needed()

            return {
                "name": name,
                "title": title,
                "path": str(path),
                "action": "updated" if existed else "created",
            }

    def delete_page(self, name: str) -> bool:
        with self._lock:
            path = self._page_path(name)
            if not path.exists():
                return False
            path.unlink()
            self._deindex_page(name)
            self._remove_links(name)
            self._rebuild_index()
            self._log_entry(action="delete", target=name, message=f"Deleted page: {name}")
            return True

    def list_pages(self, sort: str = "alpha") -> list[dict]:
        pages = []
        if not self.wiki_dir.exists():
            return pages

        for f in sorted(self.wiki_dir.glob("*.md")):
            if f.name in ("index.md", "log.md") or f.parent.name == "_captures":
                continue
            try:
                stat = f.stat()
                name = self._page_name_from_path(f)
                content = f.read_text(encoding="utf-8")
                meta, body = self._extract_frontmatter(content)
                title = meta.get("title", self._extract_title(body) or name)
                tags = self._extract_tags(content, meta)
                pages.append({
                    "name": name,
                    "title": title,
                    "tags": tags,
                    "modified": stat.st_mtime,
                    "size": stat.st_size,
                })
            except Exception:
                continue

        if sort == "mtime":
            pages.sort(key=lambda p: p["modified"], reverse=True)
        else:
            pages.sort(key=lambda p: p["name"])
        return pages

    # ── FTS5 Search with recency/tier/tag boost ───────────────────────

    def _index_page(self, name: str, title: str, content: str):
        if not self._db:
            return
        try:
            now = time.time()
            tier = "active"
            try:
                m = _FRONTMATTER_RE.match(content)
                if m:
                    import yaml
                    meta = yaml.safe_load(m.group(1)) or {}
                    tier = meta.get("tier", "active")
                    if tier not in ("active", "warm", "stale"):
                        tier = "active"
            except Exception:
                pass
            self._db.execute(
                "INSERT OR REPLACE INTO pages (path, title, modified, tier, content) VALUES (?, ?, ?, ?, ?)",
                (name, title, now, tier, content),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO pages_fts (path, title, content) VALUES (?, ?, ?)",
                (name, title, content),
            )
            self._db.commit()
        except Exception as e:
            logger.warning("FTS index error for %s: %s", name, e)

    def _deindex_page(self, name: str):
        if not self._db:
            return
        try:
            self._db.execute("DELETE FROM pages WHERE path = ?", (name,))
            self._db.execute("DELETE FROM pages_fts WHERE path = ?", (name,))
            self._db.commit()
        except Exception as e:
            logger.warning("FTS deindex error for %s: %s", name, e)

    def search(self, query: str, limit: int = 5, tag_boost: list[str] | None = None) -> list[dict]:
        """Full-text search with BM25 ranking and optional recency + tag boost.

        Args:
            query: Search terms
            limit: Max results
            tag_boost: If provided, pages whose names match these tags get a score bonus.

        Returns ranked list of dicts.
        """
        if not self._db or not query.strip():
            return []

        try:
            clean = re.sub(r'[^\w\s-]', ' ', query).strip()
            if not clean:
                return []
            terms = ' OR '.join(f'"{t}"' for t in clean.split() if len(t) > 1)
            if not terms:
                return []

            now = time.time()
            sql = """SELECT p.path, p.title,
                            snippet(pages_fts, 1, '<b>', '</b>', '...', 32) as snip,
                            bm25(pages_fts, 0, 1.0, 5.0, 5.0) as score,
                            p.modified,
                            p.tier
                     FROM pages_fts
                     JOIN pages p ON pages_fts.path = p.path
                     WHERE pages_fts MATCH ?
                     ORDER BY score
                     LIMIT ?"""
            rows = self._db.execute(sql, (terms, limit * 2)).fetchall()

            results = []
            for row in rows:
                name = row[0]
                title = row[1]
                bm25_score = row[3]
                modified = row[4]
                tier = row[5] or "active"

                age_days = (now - modified) / 86400
                recency_boost = 0.0
                if age_days < 1:
                    recency_boost = -2.0
                elif age_days < 7:
                    recency_boost = -0.5
                elif age_days < 30:
                    recency_boost = -0.1

                tag_score = 0.0
                if tag_boost:
                    name_lower = name.lower()
                    for tag in tag_boost:
                        if tag.lower() in name_lower:
                            tag_score = -1.0
                            break

                tier_map = {"stale": 5.0, "warm": 1.0, "active": 0.0}
                tier_penalty = tier_map.get(tier, 0.0)

                final_score = bm25_score + recency_boost + tag_score + tier_penalty

                results.append({
                    "name": name,
                    "title": title,
                    "snippet": row[2],
                    "score": round(final_score, 4),
                    "bm25": round(bm25_score, 4),
                    "modified": modified,
                    "tier": tier,
                })

            results.sort(key=lambda r: r["score"])
            return results[:limit]

        except sqlite3.OperationalError as e:
            logger.debug("FTS5 query error: %s", e)
            return self._fallback_search(query, limit)

    def _fallback_search(self, query: str, limit: int = 5) -> list[dict]:
        results = []
        q_lower = query.lower()
        for page in self.list_pages():
            content = self.read_page(page["name"])
            if not content:
                continue
            if q_lower in content["body"].lower() or q_lower in content["title"].lower():
                body = content["body"]
                idx = body.lower().find(q_lower)
                start = max(0, idx - 40)
                end = min(len(body), idx + len(query) + 60)
                snippet = ("..." if start > 0 else "") + body[start:end] + ("..." if end < len(body) else "")
                results.append({
                    "name": content["name"],
                    "title": content["title"],
                    "snippet": snippet,
                    "score": 0.0,
                    "bm25": 0.0,
                    "modified": content["modified"],
                })
                if len(results) >= limit:
                    break
        return results

    # ── Link Graph ────────────────────────────────────────────────────

    def _parse_links(self, content: str) -> list[str]:
        """Extract all [[wiki-links]] from page content."""
        links = _WIKI_LINK_RE.findall(content)
        # each match is (target, display_text) tuple
        targets = [link[0].strip().lower() for link in links]
        # Also extract See also section content
        parts = _SEE_ALSO_RE.split(content)
        if len(parts) > 1:
            # After "See also", extract bullet items
            see_also_part = parts[-1].split("\n#")[0]  # stop at next heading
            for line in see_also_part.split("\n"):
                line = line.strip()
                if line.startswith("- ") or line.startswith("* "):
                    ref = line.lstrip("- *").strip()
                    if ref and not ref.startswith("[") and "[" not in ref:
                        # Could be another page reference inline
                        inline_links = _WIKI_LINK_RE.findall(ref)
                        targets.extend([l[0].strip().lower() for l in inline_links])
        # Deduplicate while preserving order
        seen = set()
        deduped = []
        for t in targets:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        return deduped

    def _update_links(self, source: str, content: str):
        """Scan content for wiki-links and update the link graph."""
        if not self._db:
            return
        try:
            now = time.time()
            targets = self._parse_links(content)

            # Get existing links from this source
            existing = set()
            rows = self._db.execute(
                "SELECT target FROM links WHERE source = ?", (source,)
            ).fetchall()
            existing = {r[0] for r in rows}

            new_targets = set(targets)

            # Remove links no longer present
            for t in existing - new_targets:
                self._db.execute(
                    "DELETE FROM links WHERE source = ? AND target = ?",
                    (source, t),
                )

            # Add or update links
            for t in new_targets:
                if t in existing:
                    self._db.execute(
                        "UPDATE links SET weight = weight + 1, last_seen = ? WHERE source = ? AND target = ?",
                        (now, source, t),
                    )
                else:
                    self._db.execute(
                        "INSERT INTO links (source, target, weight, first_seen, last_seen) VALUES (?, ?, 1, ?, ?)",
                        (source, t, now, now),
                    )

            self._db.commit()
        except Exception as e:
            logger.warning("Link graph update error for %s: %s", source, e)

    def _remove_links(self, source: str):
        """Remove all links from a deleted page."""
        if not self._db:
            return
        try:
            self._db.execute("DELETE FROM links WHERE source = ?", (source,))
            self._db.commit()
        except Exception as e:
            logger.warning("Link removal error for %s: %s", source, e)

    def get_graph(self, name: str) -> dict:
        """Get link graph for a page: outgoing, incoming, and stats.

        Returns:
            {
                "page": name,
                "outgoing": [{"target": "...", "weight": N}, ...],
                "incoming": [{"source": "...", "weight": N}, ...],
                "outgoing_count": N,
                "incoming_count": N,
            }
        """
        if not self._db:
            return {"page": name, "outgoing": [], "incoming": [],
                    "outgoing_count": 0, "incoming_count": 0}

        outgoing = []
        incoming = []

        try:
            rows = self._db.execute(
                "SELECT target, weight FROM links WHERE source = ? ORDER BY weight DESC",
                (name,),
            ).fetchall()
            outgoing = [{"target": r[0], "weight": r[1]} for r in rows]

            rows = self._db.execute(
                "SELECT source, weight FROM links WHERE target = ? ORDER BY weight DESC",
                (name,),
            ).fetchall()
            incoming = [{"source": r[0], "weight": r[1]} for r in rows]
        except Exception as e:
            logger.debug("Graph query error: %s", e)

        return {
            "page": name,
            "outgoing": outgoing,
            "incoming": incoming,
            "outgoing_count": len(outgoing),
            "incoming_count": len(incoming),
        }

    # ── Captures ──────────────────────────────────────────────────────

    def add_capture(
        self,
        content: str,
        tags: str = "",
        source: str = "",
    ) -> dict:
        """Save a quick fact capture.

        Capture is stored both as a .md file in _captures/ and indexed in
        the captures SQLite table for fast querying.

        Args:
            content: The fact text (markdown allowed)
            tags: Space or comma-separated tags
            source: Where this came from (e.g. "autopilot", "user", "pre_compress")

        Returns:
            {"id": "capture-id", "path": "path/to/file", "created": timestamp}
        """
        capture_id = time.strftime("capture-%Y%m%d-%H%M%S")
        now = time.time()

        # Normalize tags
        tag_list = [t.strip() for t in tags.replace(",", " ").split() if t.strip()]
        tags_str = " ".join(tag_list)

        # Create markdown file
        file_path = self.captures_dir / f"{capture_id}.md"
        header = f"# Capture: {capture_id}\n\n"
        if tags_str:
            header += f"**Tags:** {tags_str}\n\n"
        if source:
            header += f"**Source:** {source}  \n"
        header += f"**Created:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n"

        try:
            file_path.write_text(header + content.strip() + "\n", encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to write capture file: %s", e)
            return {"error": str(e)}

        # Index in SQLite
        if self._db:
            try:
                self._db.execute(
                    "INSERT OR REPLACE INTO captures (id, content, tags, source, created, promoted) VALUES (?, ?, ?, ?, ?, 0)",
                    (capture_id, content.strip(), tags_str, source, now),
                )
                self._db.commit()
            except Exception as e:
                logger.warning("Failed to index capture: %s", e)

        self._log_entry(
            action="capture",
            target=capture_id,
            message=f"Tags: {tags_str or '(none)'} | {content.strip()[:60]}",
        )

        return {
            "id": capture_id,
            "path": str(file_path),
            "tags": tag_list,
            "created": now,
        }

    def list_captures(
        self,
        limit: int = 20,
        tag_filter: str = "",
        only_unpromoted: bool = False,
    ) -> list[dict]:
        """List recent captures.

        Args:
            limit: Max captures to return
            tag_filter: If set, only return captures with these tags
            only_unpromoted: If True, only return captures not yet promoted

        Returns:
            List of capture dicts
        """
        if not self._db:
            return []

        results = []
        try:
            sql = "SELECT id, content, tags, source, created, promoted FROM captures WHERE 1=1"
            params = []

            if only_unpromoted:
                sql += " AND promoted = 0"
            if tag_filter:
                terms = [t.strip() for t in tag_filter.replace(",", " ").split() if t.strip()]
                for term in terms:
                    sql += " AND tags LIKE ?"
                    params.append(f"%{term}%")

            sql += " ORDER BY created DESC LIMIT ?"
            params.append(limit)

            rows = self._db.execute(sql, params).fetchall()
            for r in rows:
                results.append({
                    "id": r[0],
                    "content": r[1],
                    "tags": r[2].split() if r[2] else [],
                    "source": r[3] or "",
                    "created": r[4],
                    "promoted": bool(r[5]),
                    "created_str": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(r[4])
                    ) if r[4] else "",
                })
        except Exception as e:
            logger.debug("List captures error: %s", e)

        return results

    def promote_capture(self, capture_id: str, page_name: str) -> dict:
        """Mark a capture as promoted (incorporated into a wiki page).

        Args:
            capture_id: The capture ID (e.g. "capture-20250530-120000")
            page_name: The wiki page the capture was merged into

        Returns:
            {"success": True} or {"error": "..."}
        """
        if not self._db:
            return {"error": "Database not available"}

        try:
            self._db.execute(
                "UPDATE captures SET promoted = 1 WHERE id = ?",
                (capture_id,),
            )
            self._db.commit()

            self._log_entry(
                action="promote",
                target=capture_id,
                message=f"Promoted to page: {page_name}",
            )
            return {"success": True}
        except Exception as e:
            logger.warning("Failed to promote capture %s: %s", capture_id, e)
            return {"error": str(e)}

    # ── Signals (Autopilot) ───────────────────────────────────────────

    def log_signal(
        self,
        signal_type: str,
        score: float,
        content: str,
        session_id: str = "",
    ) -> None:
        """Record an autopilot signal for analysis."""
        if not self._db:
            return
        try:
            now = time.time()
            self._db.execute(
                "INSERT INTO signals (session_id, signal_type, score, content, created) VALUES (?, ?, ?, ?, ?)",
                (session_id[:16] if session_id else "", signal_type, score, content[:500], now),
            )
            self._db.commit()
        except Exception as e:
            logger.debug("Signal log error: %s", e)

    def get_recent_signals(
        self,
        limit: int = 50,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Get recent autopilot signals."""
        if not self._db:
            return []
        try:
            sql = "SELECT id, session_id, signal_type, score, content, created FROM signals WHERE score >= ? ORDER BY created DESC LIMIT ?"
            rows = self._db.execute(sql, (min_score, limit)).fetchall()
            return [
                {
                    "id": r[0],
                    "session_id": r[1],
                    "signal_type": r[2],
                    "score": r[3],
                    "content": r[4],
                    "created": r[5],
                    "created_str": time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(r[5])
                    ) if r[5] else "",
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("Signal query error: %s", e)
            return []

    # ── Health & Stats ────────────────────────────────────────────────

    def get_health(self, wiki_dir: Path | None = None) -> dict:
        """Comprehensive health report.

        Returns:
            {
                "page_count": N,
                "capture_count": N (unpromoted),
                "tiers": {"active": N, "warm": N, "stale": N},
                "orphans": [...],  # pages with no links to/from any other page
                "broken_links": [...],  # [[targets]] that don't exist as pages
                "link_graph": {"total_links": N, "avg_outgoing": N},
                "avg_age_days": N,
                "health_score": N (0-100),
            }
        """
        stats = self.get_stats()
        health = {
            "page_count": stats["page_count"],
            "total_size_kb": stats["total_size_kb"],
            "total_size_bytes": stats["total_size_bytes"],
            "tiers": stats.get("tiers", {}),
            "avg_age_days": stats.get("avg_age_days", 0),
            "oldest_days": stats.get("oldest_days", 0),
        }

        # Capture count
        captures = self.list_captures(limit=999, only_unpromoted=True)
        health["unpromoted_captures"] = len(captures)

        # Link graph stats
        if self._db:
            try:
                row = self._db.execute(
                    "SELECT COUNT(*), COALESCE(AVG(weight), 0) FROM links"
                ).fetchone()
                health["total_links"] = row[0]
                health["avg_link_weight"] = round(row[1], 2) if row[1] else 0
            except Exception:
                health["total_links"] = 0
                health["avg_link_weight"] = 0

            try:
                row = self._db.execute(
                    "SELECT COUNT(*), COALESCE(AVG(cnt), 0) FROM (SELECT source, COUNT(*) as cnt FROM links GROUP BY source)"
                ).fetchone()
                health["link_graph"] = {
                    "total_links": health["total_links"],
                    "avg_outgoing_per_page": round(row[1], 2) if row[1] else 0,
                    "pages_with_links": row[0],
                }
            except Exception:
                health["link_graph"] = {"total_links": 0, "avg_outgoing_per_page": 0, "pages_with_links": 0}

            # Orphans: pages with no links to or from them
            health["orphans"] = self._find_orphans()

            # Broken links: [[targets]] that don't exist as pages
            health["broken_links"] = self._find_broken_links()

        health["health_score"] = self._compute_health_score(health)
        return health

    def _find_orphans(self) -> list[dict]:
        """Find pages with zero incoming or outgoing links.

        Excludes system pages (index, log, _captures).
        """
        if not self._db:
            return []

        orphans = []
        try:
            all_pages = self.list_pages()
            for p in all_pages:
                name = p["name"]
                if name.startswith("_") or name.startswith("."):
                    continue
                row = self._db.execute(
                    """SELECT
                        (SELECT COUNT(*) FROM links WHERE source = ?) +
                        (SELECT COUNT(*) FROM links WHERE target = ?)
                    """,
                    (name, name),
                ).fetchone()
                if row and row[0] == 0:
                    orphans.append({
                        "name": name,
                        "title": p["title"],
                        "size": p["size"],
                    })
        except Exception as e:
            logger.debug("Orphan query error: %s", e)

        return orphans

    def _find_broken_links(self) -> list[dict]:
        """Find [[links]] that point to pages that don't exist."""
        if not self._db:
            return []

        broken = []
        try:
            rows = self._db.execute(
                """SELECT DISTINCT l.target
                   FROM links l
                   LEFT JOIN pages p ON l.target = p.path
                   WHERE p.path IS NULL"""
            ).fetchall()

            for r in rows:
                # Count how many pages link to the missing target
                count_row = self._db.execute(
                    "SELECT COUNT(*) FROM links WHERE target = ?", (r[0],)
                ).fetchone()
                count = count_row[0] if count_row else 0

                # Get the source pages
                sources = self._db.execute(
                    "SELECT source FROM links WHERE target = ? LIMIT 5",
                    (r[0],),
                ).fetchall()

                broken.append({
                    "target": r[0],
                    "referenced_by": count,
                    "source_pages": [s[0] for s in sources],
                })
        except Exception as e:
            logger.debug("Broken links query error: %s", e)

        return broken

    def _compute_health_score(self, health: dict) -> float:
        """Compute a 0-100 health score based on multiple factors."""
        score = 100.0

        # Deductions
        if health.get("unpromoted_captures", 0) > 10:
            score -= 10  # capture backlog

        stale_pct = health.get("tiers", {}).get("stale", 0) / max(health["page_count"], 1)
        if stale_pct > 0.5:
            score -= 15
        elif stale_pct > 0.3:
            score -= 5

        broken = health.get("broken_links", [])
        if len(broken) > 5:
            score -= 10
        elif len(broken) > 0:
            score -= 5

        if health.get("total_size_kb", 0) > 500:
            score -= 5

        return max(0, round(score, 1))

    # ── Index & Log ───────────────────────────────────────────────────

    def _rebuild_index(self):
        pages = self.list_pages(sort="alpha")
        if not pages:
            return
        lines = ["# 🗂️ Memory Wiki Index", "", f"_Auto-generated. {len(pages)} pages._", ""]
        for p in pages:
            tags_str = f" `[{', '.join(p['tags'])}]`" if p['tags'] else ""
            lines.append(f"- **[[{p['name']}|{p['title']}]]**{tags_str}")
        lines.append("")
        index_path = self.root / "index.md"
        index_path.write_text("\n".join(lines), encoding="utf-8")

    def _log_entry(self, action: str, target: str, message: str = ""):
        log_path = self.root / "log.md"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"| {ts} | {action} | {target} | {message} |\n"
        try:
            if not log_path.exists():
                log_path.write_text(
                    "# 📝 Memory Wiki Log\n\n"
                    "| Timestamp | Action | Page | Details |\n"
                    "|---|---|---|---|\n",
                    encoding="utf-8",
                )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except (OSError, IOError) as e:
            logger.warning("Failed to write log: %s", e)

    def _trim_log_if_needed(self):
        log_path = self.root / "log.md"
        if not log_path.exists():
            return
        try:
            content = log_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            data_lines = [l for l in lines if l.startswith("|") and not l.startswith("|---") and "Timestamp" not in l]
            if len(data_lines) <= _MAX_LOG_ENTRIES * 2:
                return
            header_lines = [l for l in lines if not l.startswith("|") or l.startswith("|---") or "Timestamp" in l]
            keep = data_lines[-_MAX_LOG_ENTRIES:]
            trimmed = "\n".join(header_lines + keep) + "\n"
            log_path.write_text(trimmed, encoding="utf-8")
            logger.info("Trimmed log to %d entries", _MAX_LOG_ENTRIES)
        except Exception as e:
            logger.debug("Log trim skipped: %s", e)

    def read_log(self, limit: int = 50) -> list[dict]:
        log_path = self.root / "log.md"
        if not log_path.exists():
            return []
        content = log_path.read_text(encoding="utf-8")
        entries = []
        for line in content.split("\n"):
            if line.startswith("|") and not line.startswith("|---") and "Timestamp" not in line:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 3:
                    entries.append({
                        "timestamp": parts[0],
                        "action": parts[1],
                        "target": parts[2],
                        "message": parts[3] if len(parts) > 3 else "",
                    })
        return entries[-limit:]

    # ── Utility ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        pages = self.list_pages()
        total_size = sum(p["size"] for p in pages)

        # Tier counting
        tier_counts = {"active": 0, "warm": 0, "stale": 0, "unknown": 0}
        now = time.time()
        total_age = 0.0
        oldest = 0

        for p in pages:
            # Read tier from file
            page = self.read_page(p["name"])
            if page:
                tier = page["meta"].get("tier", "active") if page.get("meta") else "active"
                if tier not in tier_counts:
                    tier = "unknown"
                tier_counts[tier] += 1
                age = (now - p["modified"]) / 86400
                total_age += age
                if age > oldest:
                    oldest = age

        return {
            "page_count": len(pages),
            "total_size_bytes": total_size,
            "total_size_kb": round(total_size / 1024, 1),
            "root": str(self.root),
            "indexed": self._db is not None,
            "tiers": tier_counts,
            "avg_age_days": round(total_age / max(len(pages), 1), 1),
            "oldest_days": round(oldest, 1),
        }

    def export_as_context(self, page_names: list[str]) -> str:
        parts = []
        for name in page_names:
            page = self.read_page(name)
            if page:
                parts.append(f"--- {page['title']} ---\n{page['content']}")
            else:
                logger.debug("Wiki page '%s' not found for context export", name)
        return "\n\n".join(parts)

    def search_and_format(self, query: str, limit: int = 3, tag_boost: list[str] | None = None) -> str:
        results = self.search(query, limit=limit, tag_boost=tag_boost)
        if not results:
            return ""
        page_names = [r["name"] for r in results]
        return self.export_as_context(page_names)

    def write_raw_page(self, name: str, content: str) -> dict:
        """Write a page directly without frontmatter parsing.
        Used for programmatic writes (on_pre_compress, etc.)
        """
        with self._lock:
            path = self._page_path(name)
            existed = path.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            title = self._extract_title(content) or name
            self._index_page(name, title, content)
            self._update_links(name, content)
            self._rebuild_index()
            return {
                "name": name,
                "title": title,
                "path": str(path),
                "action": "updated" if existed else "created",
            }
