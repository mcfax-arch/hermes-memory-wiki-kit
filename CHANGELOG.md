# Changelog

## v3.0.0 (2026-05-30)

### Major features

- **Link graph**: `[[wiki-links]]` in Markdown are auto-tracked in SQLite. New tool `wiki_graph(name)` shows outgoing + incoming connections. `wiki_health()` finds orphan pages and broken links.
- **Capture system**: `wiki_capture(content, tags)` — quick fact capture without creating a full page. Stored in `wiki/_captures/` as .md files + SQLite index. CLI: `tools/wiki-capture.py`.
- **Autopilot**: Always-on signal detection in `sync_turn` and `on_pre_compress`. Detects corrections (score 5), decisions (4), preferences (4), config paths (2), URLs (1). Auto-capture when cumulative score > 8.
- **Health reporting**: `wiki_health()` tool returns page count, tier distribution, orphan pages, broken links, capture backlog, and overall health score (0-100).
- **Tag browsing**: `wiki_tags(tag)` — list/browse wiki pages by tags.

### New tools (9 total)

| Tool | Status |
|---|---|
| `wiki_search` | existing |
| `wiki_read` | existing |
| `wiki_write` | existing |
| `wiki_capture` | **new** |
| `wiki_graph` | **new** |
| `wiki_tags` | **new** |
| `wiki_ls` | existing |
| `wiki_stats` | existing |
| `wiki_health` | **new** |

### Maintenance CLI

- `tools/wiki-capture.py` — CLI quick capture tool
- `tools/wiki-maintenance.py` — full maintenance: `--decay`, `--promote`, `--health`, `--graph`
- Old `tools/memory-*.py` scripts replaced with `wiki-*.py` — clean break

### Store improvements

- New SQLite tables: `links` (link graph), `captures` (capture index), `signals` (autopilot log)
- Auto-migration: existing v2 databases get new tables automatically
- `write_page` and `write_raw_page` now auto-update link graph

## v2.0.1 (2026-05-29)

- Tier-based decay for FTS search (stale/warm/active)

## v2.0.0 (2026-05-29)

- Renamed from `memory-wiki` to `memory_wiki` (PEP 8 compliant plugin name)
- Auto-indexing of existing .md files at startup
- `on_pre_compress` hook — extracts notable patterns before context compression
- `on_session_switch` — handles /resume, /branch, /new cleanly
- `on_delegation` — captures subagent task + result pairs
- Proper replace matching using metadata.old_text
- Log auto-trimming at 500 entries
- Recency-boosted search (today, this week, this month)
- Prefetch with recency + tag boost scoring

## v1.0.0 (initial)

- Initial Hermes memory wiki kit release
- Markdown wiki with FTS5 search
- SKILL.md + AGENTS.md approach
- {MEMORY_ROOT} placeholder mechanism
