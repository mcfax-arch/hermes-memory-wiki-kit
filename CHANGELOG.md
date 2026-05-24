# Changelog

## [Unreleased]

### Added

- Добавлен portable memory-wiki kit для Hermes Agent с русским README по умолчанию и английской версией описания.
- Добавлены установщики для Windows PowerShell и macOS/Linux shell.
- Добавлены локальные scripts для capture, maintenance, graph, timeline, health, recall eval и no-LLM autopilot.
- Добавлены optional Task Scheduler и cron helper scripts для регулярного обслуживания памяти.
- Добавлен optional filesystem MCP snippet для подключения memory root к Hermes.

### Changed

- Корень памяти выбирается через `--memory-root`, `HERMES_MEMORY_ROOT`, `AI_MEMORY_ROOT` или fallback `~/Hermes_Memory`, чтобы kit не зависел от конкретного диска или ОС.

