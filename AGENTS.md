# Hermes Memory Wiki — AGENTS.md

This repository contains a **MemoryProvider plugin** for Hermes Agent.
The plugin is at `plugins/memory_wiki/`. Install it to `{HERMES_HOME}/plugins/memory_wiki/`.

## What's new in v3

The plugin provides **9 tools** for persistent Markdown wiki memory:

- **wiki_search/read/write** — core CRUD (FTS5 search)
- **wiki_capture** — quick fact capture
- **wiki_graph** — link graph from [[wiki-links]]
- **wiki_tags** — browse by tags
- **wiki_ls/stats/health** — listing, stats, health reports

### Key improvements

- **Link graph**: `[[wiki-links]]` auto-tracked in SQLite. `wiki_graph(name)` shows outgoing + incoming connections. `wiki_health()` finds orphans and broken links.
- **Capture system**: One-liner fact capture via `wiki_capture(content, tags)`. Stored in `wiki/_captures/`. CLI: `tools/wiki-capture.py`.
- **Autopilot**: Always-on signal detection in sync_turn and on_pre_compress. Auto-capture when score > 8.
- **Health**: `wiki_health()` returns page count, tier distribution, orphans, broken links, capture backlog, health score.

### Hooks

- `on_session_end` — session summary + auto-capture of session signals
- `on_memory_write` — mirrors built-in memory to wiki pages
- `on_pre_compress` — v3 enhanced with signal scoring + auto-capture
- `on_session_switch` — handles /resume, /branch, /new
- `on_delegation` — captures subagent task+result pairs

### CLI tools

```bash
# Quick capture
python tools/wiki-capture.py "fact" --tags "tag1 tag2"

# Full maintenance (decay + promote captures + health + graph)
python tools/wiki-maintenance.py
python tools/wiki-maintenance.py --health
python tools/wiki-maintenance.py --graph
```
