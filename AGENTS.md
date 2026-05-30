# Hermes Memory Wiki — AGENTS.md

## Context for Hermes Agent

This repository contains a **MemoryProvider plugin** for Hermes Agent.
The plugin is at `plugins/memory_wiki/`. Install it to `{HERMES_HOME}/plugins/memory_wiki/`.

## What's new in v3

The plugin now provides **9 tools** for persistent Markdown wiki memory:

- **wiki_search/read/write** — core CRUD (FTS5 search)
- **wiki_capture** — quick fact capture (new in v3)
- **wiki_graph** — link graph from [[wiki-links]] (new in v3)
- **wiki_tags** — browse by tags (new in v3)
- **wiki_ls/stats/health** — listing, stats, health reports

### Key improvements over v2

- **Link graph**: `[[wiki-links]]` auto-tracked in SQLite. `wiki_graph(name)` shows outgoing + incoming connections. `wiki_health()` finds orphans and broken links.
- **Capture system**: One-liner fact capture via `wiki_capture(content, tags)`. Stored in `wiki/_captures/`. CLI: `tools/wiki-capture.py`.
- **Autopilot**: Always-on signal detection. In `sync_turn` and `on_pre_compress`, messages are scored for corrections (5), decisions (4), preferences (4), config paths (2), URLs (1). Auto-capture when score > 8.
- **Health**: `wiki_health()` returns page count, tier distribution, orphans, broken links, capture backlog, health score.

### Hooks

The plugin hooks into Hermes lifecycle:
- `on_session_end` — session summary + auto-capture of session signals
- `on_memory_write` — mirrors built-in memory to wiki pages
- `on_pre_compress` — enhanced v3: signal scoring + auto-capture + note extraction
- `on_session_switch` — handles /resume, /branch, /new
- `on_delegation` — captures subagent task+result pairs

### CLI tools (standalone, work without Hermes)

```bash
# Quick capture
python tools/wiki-capture.py "fact" --tags "tag1 tag2"

# Full maintenance (decay + promote + health + graph)
python tools/wiki-maintenance.py [--decay] [--promote] [--health] [--graph]
```

### Migration from v2

Copy the updated `plugins/memory_wiki/` directory. On first load, the SQLite database is automatically migrated with new tables (links, captures, signals). No manual steps needed.
