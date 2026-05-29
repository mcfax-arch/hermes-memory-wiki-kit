"""Memory-wiki storage engine: Markdown wiki with FTS5 search.

Stores memory as human-readable Markdown files in a wiki directory.
Uses Python's built-in sqlite3 with FTS5 for full-text search.
No external dependencies -- pure stdlib.

Improvements over v1:
- Auto-indexing of existing .md files at startup
- Log trimming (keeps last N entries)
- Recency scoring in search results
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
_MAX_LOG_ENTRIES = 500


class WikiStore:
    """Markdown wiki storage engine.

    Directory layout:
      {root}/wiki/          -- Markdown pages (*.md)
      {root}/index.md       -- Auto-generated table of contents
      {root}/log.md         -- Chronological log of memory writes
      {root}/.state/db      -- SQLite FTS5 search index

    Thread-safe: all public methods use a per-instance RLock.
    """

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
    def state_dir(self) -> Path:
        return self._root / ".state"

    def _ensure_dirs(self):
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
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
        self._db.commit()

    def close(self):
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    # ------------------------------------------------------------------
    # Auto-indexing: scan all .md files and index any not in FTS
    # ------------------------------------------------------------------

    def index_all_existing(self) -> dict:
        """Scan the wiki directory and index all .md files not yet in FTS.

        Returns dict with counts: indexed, skipped, errors.
        """
        if not self.wiki_dir.exists():
            return {"indexed": 0, "skipped": 0, "errors": 0}

        # Get already-indexed pages
        indexed = set()
        if self._db:
            try:
                rows = self._db.execute("SELECT path FROM pages").fetchall()
                indexed = {r[0] for r in rows}
            except Exception:
                pass

        counts = {"indexed": 0, "skipped": 0, "errors": 0}

        for f in sorted(self.wiki_dir.glob("*.md")):
            if f.name in ("index.md", "log.md"):
                continue
            name = self._page_name_from_path(f)
            if name in indexed:
                counts["skipped"] += 1
                continue
            try:
                content = f.read_text(encoding="utf-8")
                title = self._extract_title(content) or name
                self._index_page(name, title, content)
                counts["indexed"] += 1
            except Exception as e:
                logger.warning("Failed to index %s: %s", f, e)
                counts["errors"] += 1

        self._rebuild_index()
        return counts

    # ------------------------------------------------------------------
    # Page CRUD
    # ------------------------------------------------------------------

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
            # inline #tags in body only
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
            self._rebuild_index()
            self._log_entry(action="delete", target=name, message=f"Deleted page: {name}")
            return True

    def list_pages(self, sort: str = "alpha") -> list[dict]:
        pages = []
        if not self.wiki_dir.exists():
            return pages

        for f in sorted(self.wiki_dir.glob("*.md")):
            if f.name in ("index.md", "log.md"):
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

    # ------------------------------------------------------------------
    # FTS5 Search with recency boost
    # ------------------------------------------------------------------

    def _index_page(self, name: str, title: str, content: str):
        if not self._db:
            return
        try:
            now = time.time()
            self._db.execute(
                "INSERT OR REPLACE INTO pages (path, title, modified, content) VALUES (?, ?, ?, ?)",
                (name, title, now, content),
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
            # FTS5 BM25 + modified-as-recency-score query
            sql = """SELECT p.path, p.title,
                            snippet(pages_fts, 1, '<b>', '</b>', '...', 32) as snip,
                            bm25(pages_fts, 0, 1.0, 5.0, 5.0) as score,
                            p.modified
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

                # Recency boost: pages modified in last 7 days get a discount on the BM25 score
                # (lower BM25 = better match, so we subtract a recency bonus)
                age_days = (now - modified) / 86400
                recency_boost = 0.0
                if age_days < 1:
                    recency_boost = -2.0  # today
                elif age_days < 7:
                    recency_boost = -0.5  # this week
                elif age_days < 30:
                    recency_boost = -0.1  # this month

                # Tag boost: if name matches any tag_boost term, bump it
                tag_score = 0.0
                if tag_boost:
                    name_lower = name.lower()
                    for tag in tag_boost:
                        if tag.lower() in name_lower:
                            tag_score = -1.0
                            break

                final_score = bm25_score + recency_boost + tag_score

                results.append({
                    "name": name,
                    "title": title,
                    "snippet": row[2],
                    "score": round(final_score, 4),
                    "bm25": round(bm25_score, 4),
                    "modified": modified,
                })

            # Re-sort by composite score, then limit
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

    # ------------------------------------------------------------------
    # Index & Log
    # ------------------------------------------------------------------

    def _rebuild_index(self):
        pages = self.list_pages(sort="alpha")
        if not pages:
            return
        lines = ["# \U0001f5c2\ufe0f Memory Wiki Index", "", f"_Auto-generated. {len(pages)} pages._", ""]
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
                    "# \U0001f4dd Memory Wiki Log\n\n"
                    "| Timestamp | Action | Page | Details |\n"
                    "|---|---|---|---|\n",
                    encoding="utf-8",
                )
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except (OSError, IOError) as e:
            logger.warning("Failed to write log: %s", e)

    def _trim_log_if_needed(self):
        """Trim log to _MAX_LOG_ENTRIES lines if it exceeds 2x that."""
        log_path = self.root / "log.md"
        if not log_path.exists():
            return
        try:
            content = log_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            data_lines = [l for l in lines if l.startswith("|") and not l.startswith("|---") and "Timestamp" not in l]
            if len(data_lines) <= _MAX_LOG_ENTRIES * 2:
                return
            # Keep header + last _MAX_LOG_ENTRIES data lines
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

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        pages = self.list_pages()
        total_size = sum(p["size"] for p in pages)
        return {
            "page_count": len(pages),
            "total_size_bytes": total_size,
            "total_size_kb": round(total_size / 1024, 1),
            "root": str(self.root),
            "indexed": self._db is not None,
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
            self._rebuild_index()
            return {
                "name": name,
                "title": title,
                "path": str(path),
                "action": "updated" if existed else "created",
            }
