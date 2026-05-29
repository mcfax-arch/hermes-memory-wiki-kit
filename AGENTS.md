# Hermes Memory Wiki — AGENTS.md

## Context for Hermes Agent

This repository contains a **MemoryProvider plugin** for Hermes Agent.
The plugin is at `plugins/memory-wiki/`. Install it to `{HERMES_HOME}/plugins/memory-wiki/`.

## What changed

The old SKILL.md + AGENTS.md approach with `{MEMORY_ROOT}` is **replaced** by a proper
Hermes MemoryProvider plugin. The plugin handles:
- Lifecycle (initialize, prefetch, sync, shutdown)
- FTS5 full-text search via SQLite with recency + tag boost
- Tool schemas (wiki_search, wiki_read, wiki_write, wiki_ls, wiki_stats)
- Mirroring built-in memory writes to wiki pages (preferences, environment)
- Automatic context injection before each turn (with recency + tag scoring)
- Pre-compression capture (saves notes and corrections before context compression)
- Session switch handling (/resume, /branch, /new)
- Delegation capture (records subagent task+result pairs)
- Log auto-trimming at 500 entries
- Auto-indexing of existing .md files at startup

## How to install

```bash
cp -r plugins/memory_wiki {HERMES_HOME}/plugins/
hermes memory setup      # select memory_wiki
hermes                   # start new session
```

## Migration from old kit

If migrating from the old `{MEMORY_ROOT}`-based approach:
1. Copy old wiki pages into `{HERMES_HOME}/memory-wiki/wiki/`
2. Run `python tools/lint_wiki.py` to validate
3. Delete old `{MEMORY_ROOT}` if desired

The plugin handles everything — no more manual skill loading, no more `{MEMORY_ROOT}`
placeholder replacement, no more fragile AGENTS.md routing rules.

## tools/

Standalone Python scripts for wiki maintenance (lint, graph, autopilot).
These are optional — the plugin works without them.
