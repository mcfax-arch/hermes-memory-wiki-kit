# Changelog

## [Unreleased]

### Changed (breaking)

- **Renamed** from `memory-wiki` → `memory_wiki` (PEP 8 compliant — Python package
  names must not contain hyphens). Directory, config key, and provider name all updated.
- **v2 rewrite** of `__init__.py` and `store.py` with 6 new features (see Added).
- `on_memory_write` replace now uses proper entry-based matching from metadata.
- Prefetch now uses recency boost + tag scoring for smarter ranking.
- Log auto-trims at 500 entries (was unbounded).

### Added (v2)

- `plugins/memory_wiki/` — renamed package:
  - `__init__.py`: v2 — `on_pre_compress`, `on_session_switch`, `on_delegation`
  - `store.py`: v2 — `index_all_existing()`, `_trim_log_if_needed()`, recency scoring
  - `plugin.yaml`: updated hooks list
- **Auto-indexing**: existing .md files in wiki/ are indexed at startup
- **on_pre_compress**: extracts notable patterns (remembers, corrections, code snippets)
  before context compression, saves to `_compressed/` page
- **on_session_switch**: handles `/resume`, `/branch`, `/new` — updates session_id,
  flushes turn counter on reset
- **on_delegation**: captures subagent task+result pairs to `_delegations/` page
- **Recency boost**: pages modified today get -2.0 score bonus, this week -0.5
- **Tag boost**: query terms boost pages with matching names

### Removed

- Old `skills/memory-wiki/SKILL.md` — replaced by plugin
- Old `mcp/config.yaml.snippet` — not needed
- Old `HERMES.md`, `SHARE_MESSAGE_RU.md` — not needed
- Old `memory-wiki` (hyphenated) directory — renamed to `memory_wiki`