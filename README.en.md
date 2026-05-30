# 📝 Hermes Memory Wiki Kit v3

**Native MemoryProvider plugin** for Hermes Agent — persistent Markdown wiki memory with FTS5 search, link graph, quick captures, and autopilot.

> 🚀 **v3** — link graph (`[[wiki-links]]`), captures, autopilot, health reporting, 9 tools.

---

## Quick Start

```bash
# 1. Copy the plugin to Hermes plugins directory:
cp -r plugins/memory_wiki ~/AppData/Local/hermes/plugins/   # Windows
# cp -r plugins/memory_wiki ~/.hermes/plugins/              # macOS/Linux

# 2. Activate via Hermes CLI:
hermes memory setup
# → Select "memory_wiki" from the list

# 3. Restart Hermes:
hermes
```

## Upgrading from v2

Auto-migration: new SQLite tables (links, captures, signals) are created automatically on first load. Just copy the files and restart Hermes.

## How It Works

The plugin implements `MemoryProvider` ABC and hooks into the Hermes lifecycle:

| Lifecycle hook | What it does |
|---|---|
| `initialize()` | Creates wiki directory + SQLite FTS5 index + auto-indexes existing .md |
| `system_prompt_block()` | Injects tool descriptions into the system prompt |
| `prefetch(query)` | Before each turn, searches wiki for relevant pages (FTS5 + recency/tier/tag boost) |
| `sync_turn(user, assistant)` | **v3: Autopilot** — scores signals (corrections, decisions, preferences), auto-captures when score > 8 |
| `on_memory_write(action, target, content)` | Mirrors built-in `memory` tool writes to wiki pages |
| `on_pre_compress(messages)` | **v3:** Enhanced signal analysis + auto-capture |
| `on_delegation(task, result)` | Saves subagent task+result pairs |
| `on_session_switch(new_id)` | Handles /resume, /branch, /new |
| `on_session_end(messages)` | Session summary + auto-capture session signals |
| `shutdown()` | Closes the store |

### Tools (9)

| Tool | Description | v3 |
|---|---|---|
| `wiki_search(query, limit)` | FTS5 search with BM25 + recency/tier/tag boost | ✓ |
| `wiki_read(name)` | Read a wiki page by name | ✓ |
| `wiki_write(name, content, message)` | Create/update a page (Markdown + YAML frontmatter) | ✓ |
| `wiki_ls(sort)` | List all pages with titles and tags | ✓ |
| `wiki_stats()` | Storage statistics | ✓ |
| **`wiki_capture(content, tags)`** | Quick fact capture without a full page | **new** |
| **`wiki_graph(name)`** | Link graph — outgoing + incoming connections | **new** |
| **`wiki_tags(tag)`** | Browse pages by tags | **new** |
| **`wiki_health()`** | Health report: orphans, broken links, tiers, backlog, score | **new** |

### Link Graph

Create connections with `[[wiki-links]]` in Markdown. The plugin tracks outgoing/incoming links, orphan pages, and broken links. Use `wiki_graph(name)` to inspect.

### Autopilot

Always-on signal detection:

| Signal | Score | Example |
|---|---|---|
| Correction | 5.0 | "No, that's wrong, do it this way" |
| Decision | 4.0 | "Let's try Kaspersky" |
| Preference | 4.0 | "I prefer free models" |
| Config/path | 2.0 | "config.yaml: model.context_length=..." |
| URL | 1.0 | "https://example.com/api" |

Auto-capture when cumulative score > 8. Fully automatic.

## Storage Layout

```
{HERMES_HOME}/memory-wiki/
├── wiki/
│   ├── preferences.md          # Mirrored from USER.md
│   ├── environment.md          # Mirrored from MEMORY.md
│   ├── index.md                # Auto-generated table of contents
│   ├── _captures/              # v3: Quick captures
│   │   └── capture-20250530-120000.md
│   ├── _compressed/            # Context compression snapshots
│   ├── _delegations/           # Subagent results
│   ├── _session-history.md     # Session summaries
│   └── ...                     # Your pages
├── index.md                    # TOC (auto)
├── log.md                      # Change log (auto)
└── .state/
    └── search.db               # SQLite FTS5 + links + captures + signals (auto)
```

## CLI Tools

Standalone Python scripts in `tools/`, work without Hermes:

```bash
# Quick capture from command line
python tools/wiki-capture.py "KPM service restarts on boot" --tags windows kpm

# List unpromoted captures
python tools/wiki-capture.py --list --unpromoted

# Full maintenance (decay + promote captures + health)
python tools/wiki-maintenance.py

# Health report
python tools/wiki-maintenance.py --health

# Link graph report
python tools/wiki-maintenance.py --graph

# Decay pass only
python tools/wiki-maintenance.py --decay
```

Suitable for cron / Task Scheduler daily maintenance.

## Plugin Structure

```
plugins/memory_wiki/
├── __init__.py       # MemoryWikiProvider(MemoryProvider) — lifecycle + 9 tools
├── store.py          # WikiStore — Markdown CRUD + SQLite FTS5 + links + captures
├── plugin.yaml       # Metadata (v3.0.0)
└── README.md         # Plugin docs
```

Zero dependencies. Uses: `os`, `re`, `json`, `sqlite3`, `threading`, `pathlib`, `logging`.

## Configuration

Via `hermes memory setup`:

- `wiki_root` — custom path (default: `{HERMES_HOME}/memory-wiki`)
- `prefetch_limit` — max pages to inject per turn (default: 3)
- `auto_capture` — mirror built-in memory + autopilot (default: true)

Or edit `plugin-config.json` in the wiki root directly.

## Requirements

- Hermes Agent (any version with `MemoryProvider` ABC, 2025+)
- Python 3.10+
- SQLite3 (built into Python)

## Changelog

See [CHANGELOG.md](CHANGELOG.md) — from v1 to v3.

## License

MIT
