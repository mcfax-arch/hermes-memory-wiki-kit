# Changelog

## [Unreleased]

### Changed (breaking)

- **Complete rewrite**: old SKILL.md + AGENTS.md + `{MEMORY_ROOT}` approach replaced by
  proper Hermes MemoryProvider plugin (`plugins/memory-wiki/`).
- Plugin implements `MemoryProvider` ABC with lifecycle hooks, FTS5 search, auto-prefetch,
  and automatic sync from built-in memory tool.
- Old `knowledge-base/tools/` directory with missing scripts removed from plugin path.
  Maintenance scripts kept in `tools/` as optional utilities.
- Old `AGENTS.md` replaced with simplified version pointing to plugin.
- `HERMES.md` removed (no longer needed — plugin is self-contained).
- Install scripts (`Install-HermesMemoryKit.ps1`, `install-hermes-memory-kit.sh`) kept
  for legacy migration path but plugin installation is now `cp -r plugins/memory-wiki {HERMES_HOME}/plugins/`.

### Added

- `plugins/memory-wiki/` — MemoryProvider plugin with:
  - `__init__.py`: MemoryWikiProvider — lifecycle, 5 tools, config schema
  - `store.py`: WikiStore — Markdown CRUD + SQLite FTS5 search
  - `plugin.yaml`: metadata for Hermes discovery
  - `README.md`: plugin docs
- Automatic prefetch: before each turn, searches wiki for relevant pages
- Automatic mirror: built-in `memory` tool writes → `preferences.md` / `environment.md`
- Session summary: recording in `_session-history.md`
- `hermes memory setup` integration: config via CLI wizard

### Removed

- Old `skills/memory-wiki/SKILL.md` — replaced by plugin
- Old `mcp/config.yaml.snippet` — not needed (plugin is self-contained)
- Old `HERMES.md` — not needed
- Old `SHARE_MESSAGE_RU.md` — not needed
- Old installer scripts reference to non-existent `tools/` files (`capture.py`, `status.py`, etc.)
