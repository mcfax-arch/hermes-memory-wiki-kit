"""Memory-wiki storage engine: Markdown wiki with FTS5 search.

Stores memory as human-readable Markdown files in a wiki directory.
Uses Python's built-in sqlite3 with FTS5 for full-text search.
No external dependencies — pure stdlib.
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

# Regex to extract YAML frontmatter
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
# Markdown heading
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
# Wiki-style links [[page]] or [[page|text]]
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
# Backlinks marker
_BACKLINK_HEADER = "## 🔗 Backlinks"


class WikiStore:
    """Markdown wiki storage engine.

    Directory layout:
      {root}/wiki/          — Markdown pages (*.md)
      {root}/index.md        — Auto-generated table of contents
      {root}/log.md          — Chronological log of memory writes
      {root}/.state/db       — SQLite FTS5 search index

    Thread-safe: all public methods use a per-instance RLock.
    """

    def __init__(self, root: str | Path, auto_init: bool = True):
        self._root = Path(root)
        self._lock = threading.RLock()
        self._db: sqlite3.Connection | None = None
        if auto_init:
            self._ensure_dirs()
            self._open_db()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def wiki_dir(self) -> Path:
        return self._root / "wiki"

    @property
    def state_dir(self) -> Path:
        return self._root / ".state"

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _ensure_dirs(self):
        """Create all required directories if they don't exist."""
        self.wiki_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _open_db(self):
        """Open or create the SQLite database with FTS5."""
        db_path = self.state_dir / "search.db"
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=OFF")  # safe for single-user
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
        """Close the database connection."""
        if self._db:
            try:
                self._db.close()
            except Exception:
                pass
            self._db = None

    # ------------------------------------------------------------------
    # Page CRUD — direct file operations on .md files
    # ------------------------------------------------------------------

    def _page_path(self, name: str) -> Path:
        """Convert a page name to filesystem path.

        Page name can be a simple name ("preferences") or a relative
        subpath ("projects/my-project").  Always gets .md extension.
        """
        safe = name.replace("..", "_").replace("/", os.sep).replace("\\", "_")
        return (self.wiki_dir / safe).with_suffix(".md")

    def _page_name_from_path(self, path: Path) -> str:
        """Convert a filesystem path back to a page name."""
        rel = path.relative_to(self.wiki_dir)
        stem = rel.with_suffix("")
        return str(stem.as_posix())

    def _extract_frontmatter(self, content: str) -> tuple[dict, str]:
        """Extract YAML frontmatter and body from markdown content.

        Returns (metadata_dict, body_string).
        """
        m = _FRONTMATTER_RE.match(content)
        if not m:
            return {}, content
        import yaml
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except Exception:
            meta = {}
        body = content[m.end() :]
        return meta, body

    def _render_frontmatter(self, meta: dict) -> str:
        """Render a dict as YAML frontmatter string.

        Uses a simple hand-rolled serializer to avoid pyyaml dependency
        for basic cases.  Falls back to pyyaml if available.
        """
        if not meta:
            return ""
        try:
            import yaml
            lines = yaml.dump(meta, default_flow_style=False, allow_unicode=True).strip()
        except ImportError:
            lines = "\n".join(f"{k}: {v}" for k, v in meta.items() if not isinstance(v, (dict, list)))
        return f"---\n{lines}\n---\n"

    def _extract_title(self, content: str) -> str:
        """Extract the first heading (# Title) as page title."""
        m = _HEADING_RE.search(content)
        return m.group(1).strip() if m else ""

    def _extract_tags(self, content: str, meta: dict) -> list[str]:
        """Extract tags from frontmatter or inline #tags."""
        tags = []
        if "tags" in meta:
            raw = meta["tags"]
            if isinstance(raw, str):
                tags = [t.strip() for t in raw.replace(",", " ").split()]
            elif isinstance(raw, list):
                tags = [str(t) for t in raw]
        else:
            tags = re.findall(r"(?<!\w)#(\w[\w-]*)", content.split("\n---\n")[-1])
        return tags

    # ------------------------------------------------------------------
    # Read / Write / Delete / List
    # ------------------------------------------------------------------

    def page_exists(self, name: str) -> bool:
        return self._page_path(name).exists()

    def read_page(self, name: str) -> dict | None:
        """Read a wiki page.

        Returns dict with keys: name, title, path, content, meta, tags,
        modified, or None if page doesn't exist.
        """
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
        """Create or update a wiki page.

        If content already has frontmatter, it's preserved.  Otherwise,
        the provided meta dict is prepended as frontmatter.  Updates
        the search index automatically.

        Returns dict with name, title, path, action ("created"/"updated").
        """
        with self._lock:
            path = self._page_path(name)
            existed = path.exists()

            # Check if content already has frontmatter
            existing_meta, existing_body = self._extract_frontmatter(content)
            if existing_meta:
                # Content already has its own frontmatter — use it
                if meta:
                    existing_meta.update(meta)
                final_meta = existing_meta
                final_body = existing_body
            else:
                final_meta = meta or {}
                final_body = content

            # Inject title into frontmatter if not present
            if "title" not in final_meta:
                extracted = self._extract_title(final_body)
                if extracted:
                    final_meta["title"] = extracted

            # Build final content
            front = self._render_frontmatter(final_meta) if final_meta else ""
            final_content = front + final_body

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(final_content, encoding="utf-8")

            # Update search index
            title = final_meta.get("title", self._extract_title(final_body) or name)
            self._index_page(name, title, final_content)

            # Update index.md
            self._rebuild_index()

            # Update log
            if commit_message:
                self._log_entry(
                    action="write" if existed else "create",
                    target=name,
                    message=commit_message,
                )

            return {
                "name": name,
                "title": title,
                "path": str(path),
                "action": "updated" if existed else "created",
            }

    def delete_page(self, name: str) -> bool:
        """Delete a wiki page. Returns True if it existed."""
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
        """List all wiki pages with metadata.

        Args:
            sort: "alpha" (by name), "mtime" (by modification time, newest first)

        Returns list of dicts: name, title, path, tags, modified, size.
        """
        pages = []
        if not self.wiki_dir.exists():
            return pages

        for f in sorted(self.wiki_dir.glob("*.md")):
            if f.name in ("index.md", "log.md"):
                continue
            try:
                stat = f.stat()
                page = self.read_page(self._page_name_from_path(f))
                if page:
                    pages.append({
                        "name": page["name"],
                        "title": page["title"],
                        "path": page["path"],
                        "tags": page["tags"],
                        "modified": page["modified"],
                        "size": page["size"],
                    })
            except Exception:
                continue

        if sort == "mtime":
            pages.sort(key=lambda p: p["modified"], reverse=True)
        else:
            pages.sort(key=lambda p: p["name"])

        return pages

    # ------------------------------------------------------------------
    # Search (FTS5)
    # ------------------------------------------------------------------

    def _index_page(self, name: str, title: str, content: str):
        """Add or update a page in the FTS index."""
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
        """Remove a page from the FTS index."""
        if not self._db:
            return
        try:
            self._db.execute("DELETE FROM pages WHERE path = ?", (name,))
            self._db.execute("DELETE FROM pages_fts WHERE path = ?", (name,))
            self._db.commit()
        except Exception as e:
            logger.warning("FTS deindex error for %s: %s", name, e)

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Full-text search across wiki pages using FTS5.

        Uses BM25 ranking. Returns list of dicts with:
        name, title, snippet, path, score, modified.
        """
        if not self._db or not query.strip():
            return []

        results = []
        try:
            # FTS5 query — escape special chars, use prefix matching
            clean = re.sub(r'[^\w\s-]', ' ', query).strip()
            if not clean:
                return []
            terms = ' OR '.join(f'"{t}"' for t in clean.split() if len(t) > 1)
            if not terms:
                return []

            sql = """SELECT p.path, p.title, snippet(pages_fts, 1, '<b>', '</b>', '...', 32) as snip,
                            bm25(pages_fts, 0, 1.0, 5.0, 5.0) as score, p.modified
                     FROM pages_fts
                     JOIN pages p ON pages_fts.path = p.path
                     WHERE pages_fts MATCH ?
                     ORDER BY score
                     LIMIT ?"""
            rows = self._db.execute(sql, (terms, limit)).fetchall()
            for row in rows:
                results.append({
                    "name": row[0],
                    "title": row[1],
                    "snippet": row[2],
                    "score": round(row[3], 4),
                    "modified": row[4],
                })
        except sqlite3.OperationalError as e:
            logger.debug("FTS5 query error: %s", e)
            # Fallback: simple substring search
            return self._fallback_search(query, limit)

        return results

    def _fallback_search(self, query: str, limit: int = 5) -> list[dict]:
        """Simple substring-based fallback when FTS5 query fails."""
        results = []
        q_lower = query.lower()
        for page in self.list_pages():
            content = self.read_page(page["name"])
            if not content:
                continue
            if q_lower in content["body"].lower() or q_lower in content["title"].lower():
                # Find a snippet
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
                    "modified": content["modified"],
                })
                if len(results) >= limit:
                    break
        return results

    # ------------------------------------------------------------------
    # Index & Log
    # ------------------------------------------------------------------

    def _rebuild_index(self):
        """Regenerate index.md from all pages."""
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
        """Append a timestamped entry to log.md."""
        log_path = self.root / "log.md"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"| {ts} | {action} | {target} | {message} |\n"
        try:
            # Ensure header exists
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

    def read_log(self, limit: int = 50) -> list[dict]:
        """Read recent log entries."""
        log_path = self.root / "log.md"
        if not log_path.exists():
            return []
        content = log_path.read_text(encoding="utf-8")
        entries = []
        for line in content.split("\n"):
            if line.startswith("|") and not line.startswith("|---") and not line.startswith("| Timestamp"):
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
        """Return storage statistics."""
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
        """Return one or more pages concatenated for context injection.

        Each page is fenced with its title for LLM consumption.
        Handles missing pages gracefully (skips with a note).
        """
        parts = []
        for name in page_names:
            page = self.read_page(name)
            if page:
                parts.append(f"--- {page['title']} ---\n{page['content']}")
            else:
                logger.debug("Wiki page '%s' not found for context export", name)
        return "\n\n".join(parts)

    def search_and_format(self, query: str, limit: int = 3) -> str:
        """Search wiki pages and format results for LLM context injection.

        Returns formatted string with matching pages, or empty string.
        """
        results = self.search(query, limit=limit)
        if not results:
            return ""

        # Load full pages for the top results
        page_names = [r["name"] for r in results]
        return self.export_as_context(page_names)
