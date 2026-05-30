"""Memory Wiki Plugin v3 -- with captures, link graph, and autopilot.

Provides a proper MemoryProvider implementation that stores durable
knowledge as editable Markdown files with FTS5 search, link graph,
quick-capture system, and always-on autopilot signal detection.

v3 additions:
  - Capture system: wiki_capture tool + auto-promote maintenance
  - Link graph: [[wiki-links]] are auto-tracked, wiki_graph tool
  - Health reporting: wiki_health tool (orphans, broken links, tiers, backlog)
  - Autopilot: signal scoring in on_pre_compress and sync_turn
  - wiki_tags tool: browse wiki by tags
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
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

# Autopilot scoring thresholds
AUTOPILOT_CAPTURE_THRESHOLD = 8.0  # cumulative score triggers auto-capture
SIGNAL_CORRECTION = 5.0
SIGNAL_DECISION = 4.0
SIGNAL_PREFERENCE = 4.0
SIGNAL_CODE = 2.0
SIGNAL_URL = 1.0
SIGNAL_LONG = 0.5  # per line of notable content


# ---------------------------------------------------------------------------
# Tool schemas
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
        "\nUse descriptive page names like: 'preferences', 'environment', 'projects/my-project', 'workflows/deploy'.\n"
        "\nTIP: Use [[wiki-links]] (double-bracket notation) to connect pages "
        "-- they're auto-tracked in the link graph!"
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
        "Show memory wiki statistics: page count, total size, storage path, tier distribution."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

WIKI_CAPTURE_SCHEMA = {
    "name": "wiki_capture",
    "description": (
        "Quickly save a fact or observation to the memory wiki. "
        "Use this when something is worth remembering but doesn't need a full wiki page yet -- "
        "e.g. a user preference, environment detail, project observation, or error lesson. "
        "Captures are stored separately and can be promoted to full wiki pages later via maintenance."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact or observation to save. Markdown allowed. Keep it concise."
            },
            "tags": {
                "type": "string",
                "description": "Space or comma-separated tags for categorization (e.g. 'hermes config error').",
                "default": "",
            },
        },
        "required": ["content"],
    },
}

WIKI_GRAPH_SCHEMA = {
    "name": "wiki_graph",
    "description": (
        "Show the link graph for a wiki page: which pages it links to and which pages link to it. "
        "Links are created automatically from [[wiki-links]] in page content. "
        "Use this to discover connections between pages and find related information."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Page name to inspect (e.g. 'preferences'). Use wiki_ls to list pages."
            },
        },
        "required": ["name"],
    },
}

WIKI_HEALTH_SCHEMA = {
    "name": "wiki_health",
    "description": (
        "Get a comprehensive health report for the memory wiki: "
        "page count, tier distribution (active/warm/stale), orphan pages "
        "(pages with no incoming or outgoing links), broken links "
        "([[targets]] that don't exist), capture backlog, and overall health score."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}

WIKI_TAGS_SCHEMA = {
    "name": "wiki_tags",
    "description": (
        "List or search wiki pages by tags. "
        "Returns pages with matching tags, grouped by tag."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tag": {
                "type": "string",
                "description": "Optional tag to filter by. Omit to see all tags."
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Provider class
# ---------------------------------------------------------------------------

class MemoryWikiProvider(MemoryProvider):
    """Markdown wiki memory provider for Hermes Agent, v3."""

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

    # -- Core lifecycle ------------------------------------------------

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
                "Memory-wiki v3 initialized at %s (session=%s)",
                root, session_id,
            )
        except Exception as e:
            logger.warning("Failed to initialize memory-wiki store: %s", e)
            self._initialized = False

    # -- System prompt block -------------------------------------------

    def system_prompt_block(self) -> str:
        if not self._initialized:
            return ""
        return (
            "📚 MEMORY WIKI (persistent)\n"
            "You have a persistent Markdown wiki at your disposal:\n"
            "  wiki_search(query)    -- search stored knowledge (FTS5 + recency boost)\n"
            "  wiki_read(name)       -- read a wiki page\n"
            "  wiki_write(name, content, message) -- save durable knowledge\n"
            "  wiki_capture(content, tags) -- quick fact capture (no full page needed)\n"
            "  wiki_graph(name)      -- show page connections in the link graph\n"
            "  wiki_tags(tag)        -- browse pages by tags\n"
            "  wiki_ls(sort)         -- list all pages\n"
            "  wiki_stats()          -- storage statistics\n"
            "  wiki_health()         -- health report (orphans, broken links, tiers)\n\n"
            "The wiki persists across all sessions, is FTS5-searchable, and auto-syncs\n"
            "from the built-in memory tool. Built-in stays for compact surface facts;\n"
            "depth and detail go in the wiki.\n"
            "Use [[wiki-links]] in page content to connect pages -- they're auto-tracked."
        )

    # -- Prefetch (recall before each turn) ----------------------------

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Search wiki for pages relevant to the current turn's query.

        v2: Uses tag_boost from query terms for smarter ranking.
        """
        if not self._initialized or not self._store or not query.strip():
            return ""

        try:
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

    # -- Turn sync (autopilot) -----------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """v3: Autopilot -- analyze turn for signals and auto-capture high-scoring content."""
        if not self._initialized or not self._store:
            return
        self._turn_count += 1

        # Only run autopilot if auto_capture is enabled
        if not self._auto_capture:
            return

        try:
            # Scan user message for signal patterns
            signals = self._detect_signals(user_content)
            total_score = sum(s["score"] for s in signals)
            notable_lines = []

            if signals:
                for sig in signals:
                    self._store.log_signal(
                        signal_type=sig["type"],
                        score=sig["score"],
                        content=sig["text"],
                        session_id=self._session_id,
                    )
                    notable_lines.append(
                        f"[{sig['type']}] ({sig['score']}) {sig['text']}"
                    )

            # Also scan assistant response for things worth remembering
            if assistant_content:
                # Long, technically detailed responses may contain important info
                code_blocks = re.findall(
                    r"```[\w]*\n(.*?)```", assistant_content, re.DOTALL
                )
                if code_blocks:
                    code_score = min(len(code_blocks), 10) * SIGNAL_CODE
                    total_score += code_score

                # Extract "Key takeaway" / "Note:" / "Remember:" patterns
                for line in assistant_content.split("\n"):
                    ll = line.lower().strip()
                    if any(
                        word in ll
                        for word in [
                            "remember:",
                            "note:",
                            "important:",
                            "key insight",
                            "key takeaway",
                            "lesson learned",
                            "fixed by",
                            "root cause",
                            "🔑",
                        ]
                    ):
                        total_score += SIGNAL_PREFERENCE
                        notable_lines.append(f"[assistant-note] {line.strip()[:150]}")

            # Auto-capture if score exceeds threshold
            if total_score >= AUTOPILOT_CAPTURE_THRESHOLD and notable_lines:
                capture_text = (
                    f"**Session:** {self._session_id[:16]}\n"
                    f"**Turn:** {self._turn_count}\n"
                    + "\n".join(notable_lines[:8])
                )
                self._store.add_capture(
                    content=capture_text,
                    tags="autopilot signal",
                    source="autopilot",
                )
                logger.debug(
                    "Autopilot: auto-captured turn %d (score=%.1f)",
                    self._turn_count,
                    total_score,
                )

        except Exception as e:
            logger.debug("Memory-wiki autopilot error: %s", e)

    def _detect_signals(self, text: str) -> list[dict]:
        """Detect knowledge signals in user message."""
        signals = []
        lower = text.lower()

        # Correction signals
        if any(word in lower for word in ["не так", "wrong", "incorrect", "no,", "нет,", "actually"]):
            # Find the actual correction content
            lines = text.split("\n")
            for line in lines:
                ll = line.lower().strip()
                if any(word in ll for word in ["не так", "wrong", "incorrect", "no,", "нет,"]):
                    signals.append({
                        "type": "correction",
                        "score": SIGNAL_CORRECTION,
                        "text": line.strip()[:200],
                    })
                    break

        # Preference / decision signals
        pref_hints = [
            "я хочу", "хотел", "предпочитаю", "лучше", "давай",
            "i want", "i prefer", "let's", "i'd like",
            "не надо", "не нужно", "don't", "stop",
            "запомни", "remember", "всегда", "always",
            "никогда", "never",
        ]
        for line in text.split("\n"):
            ll = line.lower().strip()
            if any(hint in ll for hint in pref_hints) and len(line) > 15:
                signals.append({
                    "type": "preference",
                    "score": SIGNAL_PREFERENCE,
                    "text": line.strip()[:200],
                })
                break

        # Decision signals ("давай попробуем", "выбираю", "начнём с")
        decision_hints = [
            "давай попробуем", "выбираю", "начнём с", "начинаем с",
            "делаем", "будем использовать", "используем",
            "let's try", "let's use", "let's start", "we'll use",
            "go with", "choose",
        ]
        for hint in decision_hints:
            if hint in lower:
                # Find the full line containing this decision
                for line in text.split("\n"):
                    if hint in line.lower():
                        signals.append({
                            "type": "decision",
                            "score": SIGNAL_DECISION,
                            "text": line.strip()[:200],
                        })
                        break
                break

        # URL signals (important resources)
        urls = re.findall(r"https?://[^\s)]+", text)
        for url in urls[:3]:
            signals.append({
                "type": "url",
                "score": SIGNAL_URL,
                "text": url[:200],
            })

        # File path signals (configuration items, project files)
        paths = re.findall(r"[A-Za-z]:\\[^\s)]+|/[A-Za-z0-9_/.-]+", text)
        for path in paths[:3]:
            if any(ext in path for ext in [".py", ".yaml", ".json", ".txt", ".md", ".env"]):
                signals.append({
                    "type": "config",
                    "score": SIGNAL_CODE,
                    "text": path[:200],
                })

        return signals

    # -- on_pre_compress: enhanced with signal scoring -----------------

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Called before context compression.

        v3: Enhanced signal scoring with auto-capture and extraction of:
        - Corrections, decisions, preferences
        - Code snippets, file paths, URLs
        - Notable assistant responses

        Saves a compressed snapshot to _compressed/ page.
        Returns empty string (no injection into compression prompt).
        """
        if not self._initialized or not self._store or not messages:
            return ""

        try:
            extracted = []
            user_msgs = []
            last_correction = None
            accumulated_score = 0.0
            captured_content = []

            for msg in messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if not content:
                    continue

                if role == "user":
                    user_msgs.append(content)

                    # Signal detection (same as autopilot)
                    signals = self._detect_signals(content)
                    for sig in signals:
                        accumulated_score += sig["score"]
                        captured_content.append(sig["text"])

                    # Detection of corrections
                    if any(word in content.lower() for word in
                           ["не так", "wrong", "incorrect", "no,", "нет,", "actually"]):
                        # Get full correction context
                        lines = content.split("\n")
                        correction_lines = [
                            l.strip() for l in lines
                            if any(w in l.lower() for w in
                                   ["не так", "wrong", "incorrect", "no,", "нет,", "correct", "исправ"])
                        ]
                        last_correction = "\n".join(correction_lines[:3])[:300]

                elif role == "assistant" and isinstance(content, str):
                    for line in content.split("\n"):
                        ll = line.lower()
                        if any(word in ll for word in
                               ["remember:", "note:", "important:",
                                "key insight", "key takeaway",
                                "lesson learned", "pitfall",
                                "🔑", "✅", "done:", "fixed"]):
                            extracted.append(line.strip()[:200])

            # Auto-capture high-scoring content
            if accumulated_score >= AUTOPILOT_CAPTURE_THRESHOLD and captured_content:
                capture_text = (
                    f"**Source:** pre_compress\n"
                    f"**Session:** {self._session_id[:16]}\n"
                    + "\n".join(captured_content[:8])
                )
                self._store.add_capture(
                    content=capture_text,
                    tags="pre_compress",
                    source="pre_compress",
                )

            # Save compressed snapshot
            if not extracted and not last_correction:
                return ""

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
                    "# 📖 Compressed Context Snapshots\n\n"
                    "_Auto-captured before context compression._\n\n"
                ) + entry

            self._store.write_raw_page(page_name, new_content)
        except Exception as e:
            logger.debug("Memory-wiki on_pre_compress: %s", e)

        return ""

    # -- Session end ---------------------------------------------------

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

            # v3: include turn count and autopilot signals
            summary = {
                "session_id": self._session_id,
                "turns": self._turn_count,
                "ended_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "note": "",
            }
            entries.append(summary)
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

            # v3: auto-capture session summary as a capture
            if self._auto_capture and self._turn_count > 3:
                signals = self._store.get_recent_signals(limit=10, min_score=1.0)
                if signals:
                    sig_summary = "\n".join(
                        f"- [{s['signal_type']}] {s['content'][:100]}"
                        for s in signals[:5]
                    )
                    capture_text = (
                        f"**Session end — {self._turn_count} turns**\n\n"
                        f"**Signals captured:**\n{sig_summary}"
                    )
                    self._store.add_capture(
                        content=capture_text,
                        tags="session-end",
                        source="session_end",
                    )

            self._store._log_entry(
                action="session_end",
                target=self._session_id[:16],
                message=f"{self._turn_count} turns",
            )
        except Exception as e:
            logger.debug("Memory-wiki on_session_end: %s", e)

    # -- Session switch -------------------------------------------------

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        **kwargs,
    ) -> None:
        if not new_session_id:
            return
        if reset:
            self._turn_count = 0
            self._session_ended = False

        old_id = self._session_id
        self._session_id = new_session_id

        logger.debug(
            "Memory-wiki session switch: %s -> %s (reset=%s)",
            old_id[:12] if old_id else "none",
            new_session_id[:12],
            reset,
        )

    # -- Delegation ----------------------------------------------------

    def on_delegation(
        self,
        task: str,
        result: str,
        *,
        child_session_id: str = "",
        **kwargs,
    ) -> None:
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
                    "# 🤖 Subagent Delegations\n\n"
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

    # -- Memory write mirror -------------------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
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
            old_text = ""
            if metadata and isinstance(metadata, dict):
                old_text = metadata.get("old_text", "")

            if old_text and old_text in body:
                body = body.replace(f"- {old_text}", f"- {content}", 1)
            else:
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
                    entry = f"- {content}\n"
                    if entry not in body:
                        body += entry

        self._store.write_page(
            page_name,
            body,
            meta=meta,
            commit_message=f"{action} builtin-{target}: {content[:60]}",
        )

    # -- Tools ----------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            WIKI_SEARCH_SCHEMA,
            WIKI_READ_SCHEMA,
            WIKI_WRITE_SCHEMA,
            WIKI_CAPTURE_SCHEMA,
            WIKI_GRAPH_SCHEMA,
            WIKI_TAGS_SCHEMA,
            WIKI_LS_SCHEMA,
            WIKI_STATS_SCHEMA,
            WIKI_HEALTH_SCHEMA,
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
                "wiki_capture": self._handle_capture,
                "wiki_graph": self._handle_graph,
                "wiki_tags": self._handle_tags,
                "wiki_ls": self._handle_ls,
                "wiki_stats": self._handle_stats,
                "wiki_health": self._handle_health,
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

    def _handle_capture(self, args: dict) -> str:
        content = args.get("content", "")
        tags = args.get("tags", "")
        if not content:
            return json.dumps({"success": False, "error": "content is required."}, ensure_ascii=False)

        result = self._store.add_capture(content=content, tags=tags, source="user")
        if "error" in result:
            return json.dumps({"success": False, "error": result["error"]}, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "result": {
                "id": result["id"],
                "path": result["path"],
                "tags": result["tags"],
                "created_str": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "message": f"Saved capture. Tags: {', '.join(result['tags']) or '(none)'}. "
                       f"Use wiki_capture to add more, or use wiki_write when it needs a full page.",
        }, ensure_ascii=False)

    def _handle_graph(self, args: dict) -> str:
        name = args.get("name", "")
        if not name:
            return json.dumps({"success": False, "error": "Page name is required."}, ensure_ascii=False)

        graph = self._store.get_graph(name)

        # Check if page exists
        exists = self._store.page_exists(name)

        result = {
            "page": name,
            "page_exists": exists,
            "outgoing": graph["outgoing"],
            "incoming": graph["incoming"],
            "outgoing_count": graph["outgoing_count"],
            "incoming_count": graph["incoming_count"],
        }

        if not exists and graph["outgoing_count"] == 0 and graph["incoming_count"] == 0:
            result["message"] = f"Page '{name}' not found and no links reference it."
            return json.dumps({"success": True, "result": result}, ensure_ascii=False)

        msg_parts = []
        if graph["outgoing_count"] > 0:
            msg_parts.append(f"links to {graph['outgoing_count']} page(s)")
        if graph["incoming_count"] > 0:
            msg_parts.append(f"linked from {graph['incoming_count']} page(s)")

        if msg_parts:
            result["message"] = f"**{name}** " + ", ".join(msg_parts) + "."
        else:
            result["message"] = f"**{name}** has no wiki-links to or from other pages (orphan)."

        return json.dumps({"success": True, "result": result}, ensure_ascii=False)

    def _handle_tags(self, args: dict) -> str:
        tag_filter = args.get("tag", "").strip().lower()

        pages = self._store.list_pages()

        # Collect all tags and their pages
        tag_map: dict[str, list[dict]] = {}
        for p in pages:
            for t in p.get("tags", []):
                tl = t.lower()
                if tag_filter and tag_filter not in tl:
                    continue
                if tl not in tag_map:
                    tag_map[tl] = []
                tag_map[tl].append({
                    "name": p["name"],
                    "title": p["title"],
                })

        # Sort tags by page count
        sorted_tags = sorted(tag_map.items(), key=lambda x: -len(x[1]))

        result = {
            "tag_filter": tag_filter or None,
            "tags": [
                {"tag": tag, "page_count": len(pages), "pages": pages}
                for tag, pages in sorted_tags
            ],
            "total_tags": len(sorted_tags),
        }

        return json.dumps({"success": True, "result": result}, ensure_ascii=False)

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

    def _handle_stats(self, args: dict = None) -> str:
        stats = self._store.get_stats()
        return json.dumps({
            "success": True,
            "stats": stats,
        }, ensure_ascii=False)

    def _handle_health(self, args: dict = None) -> str:
        health = self._store.get_health()
        return json.dumps({
            "success": True,
            "health": health,
        }, ensure_ascii=False)

    # -- Config ---------------------------------------------------------

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
                "description": "Auto-sync built-in memory tool writes to wiki + autopilot capture (true/false, default: true).",
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

    # -- Shutdown -------------------------------------------------------

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
