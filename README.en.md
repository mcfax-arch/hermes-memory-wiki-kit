# 📝 Hermes Memory Wiki Kit

**Proper MemoryProvider plugin** for Hermes Agent — persistent Markdown wiki memory with FTS5 search, auto-prefetch, and automatic sync from built-in memory.

> 🚀 **What changed**: replaces the old SKILL.md + AGENTS.md approach with a real [Hermes MemoryProvider plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin). No more brittle file paths or manual skill loading. Works with `hermes memory setup` out of the box.

---

## Quick Start

```bash
# 1. Copy the plugin to Hermes plugins directory:
cp -r plugins/memory-wiki ~/AppData/Local/hermes/plugins/   # Windows
# cp -r plugins/memory-wiki ~/.hermes/plugins/              # macOS/Linux

# 2. Activate via Hermes CLI:
hermes memory setup
# → Select "memory-wiki" from the list

# 3. Start a new session:
hermes
```

That's it. No API keys, no config files, no external dependencies.

## How It Works

The plugin implements `MemoryProvider` ABC from Hermes Agent and integrates into the agent's lifecycle:

| Lifecycle hook | What it does |
|---|---|
| `initialize()` | Creates wiki directory + SQLite FTS5 index |
| `system_prompt_block()` | Injects tool descriptions into the system prompt |
| `prefetch(query)` | Before each turn, searches wiki for relevant pages → injects as context |
| `on_memory_write(action, target, content)` | Mirrors built-in `memory` tool writes to wiki pages |
| `on_session_end(messages)` | Records session summary to `_session-history.md` |
| `shutdown()` | Closes the store |

### Tools exposed

| Tool | Description |
|---|---|
| `wiki_search(query, limit)` | FTS5 full-text search with BM25 ranking, returns snippets |
| `wiki_read(name)` | Read a full wiki page by name |
| `wiki_write(name, content, message)` | Create/update a page (Markdown + optional YAML frontmatter) |
| `wiki_ls(sort)` | List all pages with titles and tags |
| `wiki_stats()` | Storage statistics |

## Storage Layout

```
{HERMES_HOME}/memory-wiki/             # (e.g. ~/.hermes/memory-wiki or ~/AppData/Local/hermes/memory-wiki)
├── wiki/
│   ├── preferences.md                 # Mirrored from built-in USER.md
│   ├── environment.md                 # Mirrored from built-in MEMORY.md
│   ├── index.md                       # Auto-generated table of contents
│   └── ...                            # Any pages you create via wiki_write
├── index.md                           # TOC (auto)
├── log.md                             # Change log (auto)
├── plugin-config.json                 # Your config
└── .state/
    ├── search.db                      # SQLite FTS5 index (auto)
    └── link_graph.json                # Optional, from tools/link_graph.py
```

## Comparison: Built-in vs Wiki

| | Built-in (MEMORY.md/USER.md) | Wiki (this plugin) |
|---|---|---|
| Capacity | 2,200 + 1,375 chars total | Unlimited (disk) |
| Format | `§`-delimited entries | Markdown files |
| Search | None (injected as-is) | FTS5 full-text search |
| Editable | Via `memory` tool only | Via tools + direct file edit |
| Ideal for | Surface facts, quick notes | Depth, structured knowledge |

Both work together. Built-in stays for compact always-injected surface facts. Wiki for depth.

## Maintenance Tools

The `tools/` directory contains standalone Python scripts for wiki maintenance:

```bash
# Lint: check for broken links, stale pages
python tools/lint_wiki.py wiki/

# Graph: build a link graph from wiki pages
python tools/memory-graph.py --memory-root ~/.hermes/memory-wiki

# Autopilot: run all maintenance tasks
python tools/memory-autopilot.py --memory-root ~/.hermes/memory-wiki
```

These run offline without Hermes. Useful for cron/Task Scheduler.

## Plugin Structure

```
plugins/memory-wiki/
├── __init__.py       # MemoryWikiProvider(MemoryProvider) — lifecycle + tools
├── store.py          # WikiStore — Markdown CRUD + SQLite FTS5 search
├── plugin.yaml       # Metadata for plugin discovery
└── README.md         # Plugin-specific docs
```

Zero dependencies. Uses: `os`, `re`, `json`, `sqlite3`, `threading`, `time`, `pathlib`, `logging`.

## Configuration

Via `hermes memory setup`:

- `wiki_root` — custom path (default: `{HERMES_HOME}/memory-wiki`)
- `prefetch_limit` — max pages to inject per turn (default: 3)
- `auto_capture` — mirror built-in memory writes (default: true)

Or edit `plugin-config.json` in the wiki root directly.

## Requirements

- Hermes Agent (any version with `MemoryProvider` ABC — 2025+)
- Python 3.10+
- SQLite3 (built into Python)

## Migrating from the Old Kit

If you used the old `{MEMORY_ROOT}`-based approach:

1. Copy your existing wiki pages into `{HERMES_HOME}/memory-wiki/wiki/`
2. Run `python tools/lint_wiki.py {HERMES_HOME}/memory-wiki/wiki/` to check
3. Delete the old `{MEMORY_ROOT}` if desired
4. The plugin handles everything from here

## License

MIT
