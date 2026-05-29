"""Memory Wiki Plugin v2 -- persistent Markdown wiki memory for Hermes Agent.

Provides a proper MemoryProvider implementation that stores durable
knowledge as editable Markdown files with FTS5 search.  Complements
the built-in memory (MEMORY.md/USER.md) which stays for quick surface
facts -- wiki is for depth.

v2 improvements:
  - Renamed to memory_wiki (underscore, not hyphen -- PEP 8 compliant)
  - Auto-indexes existing .md files at startup
  - on_pre_compress hook -- saves compressed messages to wiki
  - on_session_switch -- handles /resume, /branch cleanly
  - on_delegation -- captures subagent task+result pairs
  - on_memory_write replace -- proper entry-based matching
  - Prefetch with recency + tag boost scoring
  - Log auto-trimming at 500 entries
"""

from __future__ import annotations

import json
import logging
import os
import time
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

from .store import WikiStore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PREFETCH_LIMIT = 3
DEFAULT_AUTO_CAPTURE = True
COMPRESSED_PAGES_DIR = "_compressed"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

WIKI_SEARCH_SCHEMA = {
    "name": "wiki_search",
    "description": (
        "Search the memory wiki for pages matching your query. "
        "Uses full-text search (FTS5) with BM25 ranking, recency boost, and tag scoring. "
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
        "or just write markdown content -- frontmatter will be auto-generated. "
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
    """Markdown wiki memory provider for Hermes Agent, v2."""

    def __init__(self):
        self._store: WikiStore | None = None
        self._wiki_root: str | None = None
        self._session_id: str = ""
        self._hermes_home: str = ""
        self._prefetch_limit: int = DEFAULT_PREFETCH_LIMIT
        self._auto_capture: bool = DEFAULT_AUTO_CAPTURE
        self._turn_count: int = 0
        self._initialized: bool = False
        self._platform: str = "cli"
        self._session_ended: bool = False

    # -- Core lifecycle --------------------------------------------------

    @property
    def name(self) -> str:
        return "memory_wiki"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        self._hermes_home = kwargs.get("hermes_home", "")
        self._prefetch_limit = kwargs.get("prefetch_limit", DEFAULT_PREFETCH_LIMIT)
        self._auto_capture = kwargs.get("auto_capture", DEFAULT_AUTO_CAPTURE)
        self._platform = kwargs.get("platform", "cli")
        self._turn_count = 0
        self._session_ended = False

        if self._wiki_root:
            root = Path(self._wiki_root)
        elif self._hermes_home:
            root = Path(self._hermes_home) / "memory-wiki"
        else:
            root = Path.home() / ".hermes" / "memory-wiki"

        try:
            self._store = WikiStore(root)

            # v2: auto-index existing .md files
            index_result = self._store.index_all_existing()
            if index_result["indexed"] > 0:
                logger.info(
                    "Memory-wiki: indexed %d existing pages",
                    index_result["indexed"],
                )

            self._initialized = True
            logger.info(
                "Memory-wiki v2 initialized at %s (session=%s)",
                root, session_id,
            )
        except Exception as e:
            logger.warning("Failed to initialize memory-wiki store: %s", e)
            self._initialized = False

    # -- System prompt block ---------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._initialized:
            return ""
        return (
            "\U0001f4da MEMORY WIKI (persistent)\n"
            "You have a persistent Markdown wiki at your disposal:\n"
            "  wiki_search(query) -- search stored knowledge (FTS5 + recency boost)\n"
            "  wiki_read(name)    -- read a wiki page\n"
            "  wiki_write(name, content, message) -- save durable knowledge\n"
            "  wiki_ls(sort)      -- list all pages\n"
            "  wiki_stats()       -- storage statistics\n\n"
            "Save important facts to the wiki: user preferences, environment quirks,\n"
            "project conventions, workflows, and non-trivial solutions.\n"
            "The wiki persists across all sessions, is FTS5-searchable, and auto-syncs\n"
            "from the built-in memory tool. Built-in stays for compact surface facts;\n"
            "depth and detail go in the wiki."
        )

    # -- Prefetch (recall before each turn) ------------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Search wiki for pages relevant to the current turn's query.

        v2: Uses tag_boost from query terms for smarter ranking.
        """
        if not self._initialized or not self._store or not query.strip():
            return ""

        try:
            # Extract potential tag terms from query for tag_boost
            words = query.lower().split()
            tag_terms = [w for w in words if len(w) > 3][:5]

            context = self._store.search_and_format(
                query,
                limit=self._prefetch_limit,
                tag_boost=tag_terms if tag_terms else None,
            )
            return context
        except Exception as e:
            logger.debug("Memory-wiki prefetch: %s", e)
            return ""

    # -- Turn sync -------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if not self._initialized:
            return
        self._turn_count += 1

    # -- on_pre_compress: save insights before compression ---------------

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Called before context compression.

        Extracts notable patterns from messages about to be compressed:
        - Lines with "remember", "note", "important"
        - User corrections
        - Code snippets
        - URLs and file paths

        Saves a compressed snapshot to _compressed/ page.
        Returns empty string (no injection into compression prompt).
        """
        if not self._initialized or not self._store or not messages:
            return ""

        try:
            extracted = []
            user_msgs = []
            last_correction = None

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if not content:
                    continue

                if role == "user":
                    user_msgs.append(content)
                    # Detect corrections
                    if any(word in content.lower() for word in
                           ["не так", "wrong", "incorrect", "no,", "нет,", "actually"]):
                        last_correction = content[:200]
                elif role == "assistant" and isinstance(content, str):
                    # Extract notable patterns from assistant responses
                    for line in content.split("\n"):
                        ll = line.lower()
                        if any(word in ll for word in
                               ["remember:", "note:", "important:", "key insight",
                                "key takeaway", "lesson learned", "pitfall"]):
                            extracted.append(line.strip()[:200])

            if not extracted and not last_correction:
                return ""

            # Save to compressed page
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            session_short = self._session_id[:8] if self._session_id else "unknown"

            entry = f"### Compression snapshot -- {ts} (session {session_short})\n"
            if extracted:
                entry += "\n**Extracted notes:**\n"
                for note in extracted[:10]:
                    entry += f"- {note}\n"
            if last_correction:
                entry += f"\n**Correction:** {last_correction}\n"
            entry += f"\n_Messages in batch: {len(messages)}_\n\n"

            page_name = f"{COMPRESSED_PAGES_DIR}/{session_short}"

            existing = self._store.read_page(page_name)
            if existing:
                new_content = existing["body"] + "\n" + entry
            else:
                new_content = (
                    "# \U0001f4be Compressed Context Snapshots\n\n"
                    "_Auto-captured before context compression._\n\n"
                ) + entry

            self._store.write_raw_page(page_name, new_content)
        except Exception as e:
            logger.debug("Memory-wiki on_pre_compress: %s", e)

        return ""

    # -- Session end -----------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._session_ended or not self._initialized or not self._store:
            return
        self._session_ended = True

        try:
            session_page = self._store.read_page("_session-history")
            entries = []
            if session_page and "sessions" in session_page.get("meta", {}):
                try:
                    entries = json.loads(session_page["meta"]["sessions"])
                except (json.JSONDecodeError, TypeError):
                    entries = []

            summary = {
                "session_id": self._session_id,
                "turns": self._turn_count,
                "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "",
            }
            entries.append(summary)
            entries = entries[-20:]

            content = (
                "# \U0001f4cb Session History\n\n"
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
            logger.debug("Memory-wiki on_session_end: %s", e)

    # -- Session switch (v2: handle /resume, /branch) --------------------

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        """Handle session_id rotation (e.g. /resume, /branch, /new).

        v2: properly updates internal session tracking and flushes
        per-session buffers on reset.
        """
        if not new_session_id:
            return

        # Flush accumulated state if this is a genuinely new conversation
        if reset:
            self._turn_count = 0
            self._session_ended = False

        # Update the tracked session id
        old_id = self._session_id
        self._session_id = new_session_id

        logger.debug(
            "Memory-wiki session switch: %s -> %s (reset=%s)",
            old_id[:12] if old_id else "none",
            new_session_id[:12],
            reset,
        )

    # -- Delegation (v2: capture subagent results) -----------------------

    def on_delegation(
        self,
        task: str,
        result: str,
        *,
        child_session_id: str = "",
        **kwargs,
    ) -> None:
        """Called when a subagent completes.

        Saves the task+result pair to the wiki for future reference.
        """
        if not self._initialized or not self._store:
            return
        if not task or not result:
            return

        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            task_short = task[:80].replace("\n", " ").strip()
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', task_short[:40].lower())

            page_name = f"_delegations/{safe_name or 'task'}"
            child_id = child_session_id[:8] if child_session_id else "?"

            existing = self._store.read_page(page_name)
            if existing:
                body = existing["body"] + f"\n---\n\n### {ts} (child: {child_id})\n\n**Task:** {task_short}\n\n{result}\n"
            else:
                body = (
                    "# \U0001f916 Subagent Delegations\n\n"
                    "_Auto-captured task+result pairs._\n\n"
                    f"### {ts} (child: {child_id})\n\n"
                    f"**Task:** {task_short}\n\n{result}\n"
                )

            self._store.write_raw_page(page_name, body)
            self._store._log_entry(
                action="delegation",
                target=page_name,
                message=f"Subagent: {task_short[:60]}",
            )
        except Exception as e:
            logger.debug("Memory-wiki on_delegation: %s", e)

    # -- Memory write mirror ---------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror built-in memory tool writes to wiki pages.

        v2: replace uses proper old_text matching from metadata.
        """
        if not self._initialized or not self._store or action == "remove":
            return

        page_name = "preferences" if target == "user" else "environment"
        title = "User Preferences" if target == "user" else "Environment & Conventions"

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
            # v2: use metadata.old_text for precise matching
            old_text = ""
            if metadata and isinstance(metadata, dict):
                old_text = metadata.get("old_text", "")

            if old_text and old_text in body:
                # Replace the old_text line specifically
                body = body.replace(f"- {old_text}", f"- {content}", 1)
            else:
                # Fallback: find any entry containing old_text substring
                # Look for list items containing the old_text
                lines = body.split("\n")
                new_lines = []
                replaced = False
                for line in lines:
                    stripped = line.strip()
                    if not replaced and stripped.startswith("- ") and old_text and old_text in stripped:
                        new_lines.append(f"- {content}" if line.startswith("-") else f"  - {content}")
                        replaced = True
                    else:
                        new_lines.append(line)
                if replaced:
                    body = "\n".join(new_lines)
                else:
                    # If nothing matched, just append
                    entry = f"- {content}\n"
                    if entry not in body:
                        body += entry

        self._store.write_page(
            page_name,
            body,
            meta=meta,
            commit_message=f"{action} builtin-{target}: {content[:60]}",
        )

    # -- Tools -----------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            WIKI_SEARCH_SCHEMA,
            WIKI_READ_SCHEMA,
            WIKI_WRITE_SCHEMA,
            WIKI_LS_SCHEMA,
            WIKI_STATS_SCHEMA,
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        if not self._initialized or not self._store:
            return json.dumps({
                "success": False,
                "error": "Memory-wiki not initialized.",
            }, ensure_ascii=False)

        try:
            handlers = {
                "wiki_search": self._handle_search,
                "wiki_read": self._handle_read,
                "wiki_write": self._handle_write,
                "wiki_ls": self._handle_ls,
                "wiki_stats": self._handle_stats,
            }
            handler = handlers.get(tool_name)
            if handler:
                return handler(args)
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

    # -- Config ----------------------------------------------------------

    def get_config_schema(self) -> List[Dict[str, Any]]:
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
        config_dir = Path(hermes_home) / "memory-wiki"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "plugin-config.json"

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

    # -- Shutdown --------------------------------------------------------

    def shutdown(self) -> None:
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
    ctx.register_memory_provider(MemoryWikiProvider())
