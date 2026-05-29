"""Memory Wiki Plugin — persistent Markdown wiki memory for Hermes Agent.

Provides a proper MemoryProvider implementation that stores durable
knowledge as editable Markdown files with FTS5 search.  Complements
the built-in memory (MEMORY.md/USER.md) which stays for quick surface
facts — wiki is for depth.

Lifecycle integration:
  - initialize()     — create storage, rebuild index
  - system_prompt_block() — brief usage instructions
  - prefetch(query)  — search wiki, inject relevant pages as context
  - sync_turn()      — maintain turn log
  - on_session_end() — compile session wrap-up
  - on_memory_write() — mirror built-in memory tool writes to wiki
  - get_tool_schemas() → tools: wiki_search, wiki_read, wiki_write, wiki_ls
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# Re-export store for potential external use
from .store import WikiStore


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

DEFAULT_PREFETCH_LIMIT = 3
DEFAULT_AUTO_CAPTURE = True


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

WIKI_SEARCH_SCHEMA = {
    "name": "wiki_search",
    "description": (
        "Search the memory wiki for pages matching your query. "
        "Uses full-text search (FTS5) with BM25 ranking. "
        "Use this when you need to recall stored knowledge about a topic. "
        "Returns snippets of matching pages."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query (keywords or phrase)."
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default: 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
}

WIKI_READ_SCHEMA = {
    "name": "wiki_read",
    "description": (
        "Read a specific page from the memory wiki by name. "
        "Use wiki_search first to find the page name, then read it here. "
        "Pages are stored as Markdown files with YAML frontmatter."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Page name (e.g. 'preferences', 'environment', 'projects/my-app')."
            },
        },
        "required": ["name"],
    },
}

WIKI_WRITE_SCHEMA = {
    "name": "wiki_write",
    "description": (
        "Create or update a page in the memory wiki. "
        "Pages are Markdown files. You can include YAML frontmatter (---\\ntitle: ...\\ntags: [tag1, tag2]\\n---) "
        "or just write markdown content — frontmatter will be auto-generated. "
        "\n\nWHEN TO WRITE:\n"
        "- User shares significant personal information (career, projects, health, goals)\n"
        "- You discover a stable fact about the environment or project that will matter later\n"
        "- User asks you to remember something\n"
        "- Encounter non-trivial error with a solution that should be saved\n"
        "\nWHEN NOT TO WRITE:\n"
        "- Session progress, current task state, temporary details\n"
        "- Things easily re-fetched from source\n"
        "- Trivial one-off facts\n"
        "\nUse descriptive page names like: 'preferences', 'environment', 'projects/my-project', 'workflows/deploy'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Page name (e.g. 'preferences', 'environment', 'tips/python')."
            },
            "content": {
                "type": "string",
                "description": "Markdown content (with optional YAML frontmatter)."
            },
            "message": {
                "type": "string",
                "description": "Short commit message for the log (optional).",
                "default": "",
            },
        },
        "required": ["name", "content"],
    },
}

WIKI_LS_SCHEMA = {
    "name": "wiki_ls",
    "description": (
        "List all pages in the memory wiki with their titles and tags. "
        "Use this to get an overview of what knowledge is stored. "
        "Sort options: 'alpha' (by name) or 'mtime' (recently modified first)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "sort": {
                "type": "string",
                "enum": ["alpha", "mtime"],
                "description": "Sort order (default: alpha).",
                "default": "alpha",
            },
        },
    },
}

WIKI_STATS_SCHEMA = {
    "name": "wiki_stats",
    "description": (
        "Show memory wiki statistics: page count, total size, storage path."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------

class MemoryWikiProvider(MemoryProvider):
    """Markdown wiki memory provider for Hermes Agent."""

    # ── Core lifecycle ──────────────────────────────────────────────────

    def __init__(self):
        self._store: WikiStore | None = None
        self._wiki_root: str | None = None
        self._session_id: str = ""
        self._hermes_home: str = ""
        self._prefetch_limit: int = DEFAULT_PREFETCH_LIMIT
        self._auto_capture: bool = DEFAULT_AUTO_CAPTURE
        self._turn_count: int = 0
        self._initialized: bool = False

    @property
    def name(self) -> str:
        return "memory-wiki"

    def is_available(self) -> bool:
        """Always available — no external deps needed."""
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        """Create storage directory and initialize the wiki store."""
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", "")
        self._prefetch_limit = kwargs.get("prefetch_limit", DEFAULT_PREFETCH_LIMIT)
        self._auto_capture = kwargs.get("auto_capture", DEFAULT_AUTO_CAPTURE)

        # Determine wiki root: from config or default to hermes_home/memory-wiki
        if self._wiki_root:
            root = Path(self._wiki_root)
        elif self._hermes_home:
            root = Path(self._hermes_home) / "memory-wiki"
        else:
            # Fallback: ~/.hermes/memory-wiki
            root = Path.home() / ".hermes" / "memory-wiki"

        try:
            self._store = WikiStore(root)
            self._initialized = True
            logger.info(
                "Memory-wiki initialized at %s (session=%s)",
                root, session_id,
            )
        except Exception as e:
            logger.warning("Failed to initialize memory-wiki store: %s", e)
            self._initialized = False

    # ── System prompt block ────────────────────────────────────────────

    def system_prompt_block(self) -> str:
        """Return usage instructions for the wiki tools."""
        if not self._initialized:
            return ""
        return (
            "═══ MEMORY WIKI ═══\n"
            "You have a persistent Markdown wiki at your disposal:\n"
            "  wiki_search(query) — search stored knowledge\n"
            "  wiki_read(name)    — read a wiki page\n"
            "  wiki_write(name, content, message) — save knowledge\n"
            "  wiki_ls(sort)      — list all pages\n"
            "  wiki_stats()       — storage statistics\n\n"
            "Save important facts to the wiki: user preferences, environment quirks,\n"
            "project conventions, workflow patterns, and non-trivial solutions.\n"
            "The wiki persists across all sessions and is searchable via FTS5.\n"
            "Built-in memory (MEMORY.md/USER.md with the memory tool) stays for\n"
            "compact surface-level facts; depth and detail go in the wiki.\n"
            "═══"
        )

    # ── Prefetch (recall before each turn) ─────────────────────────────

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Search wiki for pages relevant to the current turn's query.

        Returns formatted markdown pages wrapped in memory-context fence,
        or empty string if nothing relevant found.
        """
        if not self._initialized or not self._store or not query.strip():
            return ""

        try:
            context = self._store.search_and_format(query, limit=self._prefetch_limit)
            return context
        except Exception as e:
            logger.debug("Memory-wiki prefetch: %s", e)
            return ""

    # ── Turn sync ──────────────────────────────────────────────────────

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Log the turn to the wiki's turn log.

        Does NOT do LLM-based extraction — that's the agent's job via
        the wiki_write tool.  But we track turn stats for session-end.
        """
        if not self._initialized:
            return
        self._turn_count += 1

    # ── Session end ────────────────────────────────────────────────────

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Compile a session summary to the wiki."""
        if not self._initialized or not self._store or self._turn_count == 0:
            return
        try:
            session_page = self._store.read_page("_session-history")
            if not session_page:
                entries = []
            else:
                entries = json.loads(session_page.get("meta", {}).get("sessions", "[]"))

            # Summarize this session
            summary = {
                "session_id": self._session_id,
                "turns": self._turn_count,
                "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "",  # filled in by user or agent
            }
            entries.append(summary)
            # Keep last 20
            entries = entries[-20:]

            content = (
                "# 📋 Session History\n\n"
                "Auto-recorded sessions summary.\n\n"
                f"Total sessions logged: {len(entries)}\n\n"
            )
            for e in entries:
                content += (
                    f"### Session {e['session_id'][:8]}...\n"
                    f"- **Turns**: {e['turns']}\n"
                    f"- **Ended**: {e['ended_at']}\n"
                    f"- **Note**: {e.get('note', '')}\n\n"
                )

            self._store.write_page(
                "_session-history",
                content,
                meta={"title": "Session History", "sessions": json.dumps(entries)},
            )
            self._store._log_entry(
                action="session_end",
                target=self._session_id[:16],
                message=f"{self._turn_count} turns",
            )
        except Exception as e:
            logger.debug("Memory-wiki session_end: %s", e)

    # ── Memory write mirror ────────────────────────────────────────────

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory tool writes to wiki pages.

        MEMORY.md entries → environment page
        USER.md entries   → preferences page
        """
        if not self._initialized or not self._store or action == "remove":
            return

        page_name = "preferences" if target == "user" else "environment"
        title = "User Preferences" if target == "user" else "Environment & Conventions"

        # Read existing page
        existing = self._store.read_page(page_name)
        if existing:
            body = existing["body"]
            meta = existing["meta"]
        else:
            body = f"# {title}\n\n_Auto-synced from Hermes built-in memory._\n\n"
            meta = {"title": title, "tags": ["memory", target]}

        if action == "add":
            entry = f"- {content}\n"
            if entry not in body:
                body += entry
        elif action == "replace":
            # old text comes from metadata
            if metadata and "old_text" in metadata:
                body = body.replace(metadata["old_text"], content)

        self._store.write_page(
            page_name,
            body,
            meta=meta,
            commit_message=f"{action} builtin-{target}: {content[:60]}",
        )

    # ── Tools ──────────────────────────────────────────────────────────

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            WIKI_SEARCH_SCHEMA,
            WIKI_READ_SCHEMA,
            WIKI_WRITE_SCHEMA,
            WIKI_LS_SCHEMA,
            WIKI_STATS_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Dispatch wiki tool calls."""
        if not self._initialized or not self._store:
            return json.dumps({
                "success": False,
                "error": "Memory-wiki not initialized. Check ~/.hermes/plugins/memory-wiki/",
            }, ensure_ascii=False)

        try:
            if tool_name == "wiki_search":
                return self._handle_search(args)
            elif tool_name == "wiki_read":
                return self._handle_read(args)
            elif tool_name == "wiki_write":
                return self._handle_write(args)
            elif tool_name == "wiki_ls":
                return self._handle_ls(args)
            elif tool_name == "wiki_stats":
                return self._handle_stats()
            else:
                return json.dumps({
                    "success": False,
                    "error": f"Unknown tool: {tool_name}",
                }, ensure_ascii=False)
        except Exception as e:
            logger.error("Memory-wiki tool %s failed: %s", tool_name, e, exc_info=True)
            return json.dumps({
                "success": False,
                "error": f"Memory-wiki tool '{tool_name}' failed: {e}",
            }, ensure_ascii=False)

    def _handle_search(self, args: dict) -> str:
        query = args.get("query", "")
        limit = args.get("limit", 5)
        results = self._store.search(query, limit=limit)
        if not results:
            return json.dumps({
                "success": True,
                "results": [],
                "message": "No matching pages found.",
            }, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "results": results,
            "message": f"Found {len(results)} matching page(s). Use wiki_read(name) to view full content.",
        }, ensure_ascii=False)

    def _handle_read(self, args: dict) -> str:
        name = args.get("name", "")
        if not name:
            return json.dumps({"success": False, "error": "Page name is required."}, ensure_ascii=False)
        page = self._store.read_page(name)
        if not page:
            # Suggest similar pages
            all_pages = self._store.list_pages()
            suggestions = [p["name"] for p in all_pages if name.lower() in p["name"].lower()]
            msg = f"Page '{name}' not found."
            if suggestions:
                msg += f" Did you mean: {', '.join(suggestions[:5])}?"
            return json.dumps({"success": False, "error": msg}, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "name": page["name"],
            "title": page["title"],
            "content": page["content"],
            "body": page["body"],
            "meta": page["meta"],
            "tags": page["tags"],
            "size": page["size"],
        }, ensure_ascii=False)

    def _handle_write(self, args: dict) -> str:
        name = args.get("name", "")
        content = args.get("content", "")
        message = args.get("message", "")
        if not name or not content:
            return json.dumps({"success": False, "error": "name and content are required."}, ensure_ascii=False)
        result = self._store.write_page(name, content, commit_message=message or f"Wrote {len(content)} chars")
        return json.dumps({
            "success": True,
            "result": result,
        }, ensure_ascii=False)

    def _handle_ls(self, args: dict) -> str:
        sort = args.get("sort", "alpha")
        pages = self._store.list_pages(sort=sort)
        if not pages:
            return json.dumps({
                "success": True,
                "pages": [],
                "message": "Wiki is empty. Use wiki_write to add pages.",
            }, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "pages": pages,
            "total": len(pages),
        }, ensure_ascii=False)

    def _handle_stats(self) -> str:
        stats = self._store.get_stats()
        return json.dumps({
            "success": True,
            "stats": stats,
        }, ensure_ascii=False)

    # ── Config ─────────────────────────────────────────────────────────

    def get_config_schema(self) -> List[Dict[str, Any]]:
        """Declare config fields for 'hermes memory setup'."""
        return [
            {
                "key": "wiki_root",
                "description": (
                    "Path to the memory wiki directory. "
                    "Default: {hermes_home}/memory-wiki. "
                    "Leave blank for default."
                ),
                "default": "",
                "required": False,
            },
            {
                "key": "prefetch_limit",
                "description": "Max wiki pages to inject as context per turn (default: 3).",
                "default": "3",
                "required": False,
            },
            {
                "key": "auto_capture",
                "description": "Auto-sync built-in memory tool writes to wiki (true/false, default: true).",
                "default": "true",
                "choices": ["true", "false"],
            },
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        """Write plugin config to memory-wiki/config.json."""
        config_dir = Path(hermes_home) / "memory-wiki"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "plugin-config.json"

        # Only persist non-empty, non-default values
        config = {}
        if values.get("wiki_root"):
            config["wiki_root"] = values["wiki_root"]
        if values.get("prefetch_limit"):
            try:
                config["prefetch_limit"] = int(values["prefetch_limit"])
            except (ValueError, TypeError):
                pass
        if values.get("auto_capture"):
            config["auto_capture"] = values["auto_capture"].lower() == "true"

        if config:
            config_path.write_text(
                json.dumps(config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        elif config_path.exists():
            config_path.unlink()

    # ── Shutdown ───────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Flush and close the wiki store."""
        if self._store:
            try:
                self._store.close()
            except Exception:
                pass
            self._store = None
        self._initialized = False
        logger.info("Memory-wiki shut down")


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register the memory-wiki provider with the plugin system."""
    ctx.register_memory_provider(MemoryWikiProvider())
