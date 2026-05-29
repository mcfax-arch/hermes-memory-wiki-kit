# 📝 Memory Wiki Plugin for Hermes Agent

A proper Hermes **MemoryProvider plugin** that stores durable knowledge as
human-editable Markdown files with full-text search. No API keys, no external
dependencies — pure Python stdlib.

## Quick Start

```bash
# 1. Plugin is already installed in ~/.hermes/plugins/memory-wiki/
# 2. Activate it:
hermes memory setup

# → Select "memory-wiki" from the list → done!

# 3. Start a new session for changes to take effect:
hermes
```

## What It Does

### Automatic (zero-effort)

| What | How |
|------|-----|
| **Prefetch** | Before each turn, searches the wiki for pages relevant to your query and injects them as context |
| **Mirror** | When you use the built-in `memory` tool (MEMORY.md/USER.md), writes are mirrored to wiki pages (`preferences.md`, `environment.md`) |
| **Session log** | Each session's turn count and duration is recorded in `_session-history.md` |
| **Search index** | All pages are indexed via SQLite FTS5 for instant full-text search |

### Tools (call from Hermes)

| Tool | Description |
|------|-------------|
| `wiki_search(query, limit)` | Full-text search with BM25 ranking, returns snippets |
| `wiki_read(name)` | Read a full wiki page by name |
| `wiki_write(name, content, message)` | Create or update a page (Markdown + optional YAML frontmatter) |
| `wiki_ls(sort)` | List all pages with titles and tags |
| `wiki_stats()` | Storage statistics |

### Manual commands

```bash
# Check status
hermes memory status

# Reconfigure
hermes memory setup

# View wiki files directly
ls ~/.hermes/memory-wiki/wiki/
cat ~/.hermes/memory-wiki/index.md
```

## Storage Layout

```
~/.hermes/memory-wiki/
├── wiki/
│   ├── preferences.md        # Mirrored from USER.md
│   ├── environment.md        # Mirrored from MEMORY.md
│   ├── index.md              # Auto-generated table of contents
│   └── ...                   # Any pages you create
├── index.md                  # TOC (auto)
├── log.md                    # Change log (auto)
├── plugin-config.json        # Your config (auto)
└── .state/
    ├── search.db             # SQLite FTS5 index (auto)
    └── link_graph.json       # Link graph (optional, from tools/link_graph.py)
```

## Comparison: Built-in vs Wiki

| | Built-in (MEMORY.md/USER.md) | Wiki (this plugin) |
|---|---|---|
| Capacity | 2,200 + 1,375 chars total | Unlimited (disk) |
| Format | `§`-delimited entries | Markdown files |
| Search | None (injected as-is) | FTS5 full-text search |
| Editable | Via `memory` tool only | Via tools + direct file edit |
| Ideal for | Surface facts, quick notes | Depth, structured knowledge |
| Retention | Across sessions | Across sessions |

Both work together. The built-in memory stays for your compact, always-injected
surface facts. The wiki is for the full depth — project docs, research notes,
workflow references, anything that doesn't fit in 2K chars.

## For Developers

### How it integrates

1. Plugin is discovered by `plugins/memory/__init__.py` in `$HERMES_HOME/plugins/`
2. Activated via `memory.provider` key in `config.yaml` (set by `hermes memory setup`)
3. `MemoryManager` in `run_agent.py` calls into the plugin lifecycle:
   - `initialize()` → creates wiki store
   - `prefetch(query)` → searches wiki, injects context
   - `sync_turn()` → tracks turn count
   - `on_session_end()` → writes session summary
   - `on_memory_write()` → mirrors built-in memory writes
   - `shutdown()` → closes store
4. `get_tool_schemas()` → tools registered in Hermes tool registry

### Files

- `__init__.py` — `MemoryWikiProvider(MemoryProvider)` with lifecycle + tools
- `store.py` — `WikiStore` engine: markdown CRUD + SQLite FTS5 search
- `plugin.yaml` — metadata for plugin discovery

### Dependencies

Zero. Uses: `os`, `re`, `json`, `sqlite3`, `threading`, `time`, `pathlib`, `logging`.
YAML frontmatter uses `PyYAML` if available (bundled with Hermes), else falls
back to a simple hand-rolled serializer.
