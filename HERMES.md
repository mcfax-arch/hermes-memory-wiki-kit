# Hermes Memory Wiki Bootstrap

This project uses an external Markdown memory wiki.

Read and follow `AGENTS.md` in this same folder. If this file is loaded instead of `AGENTS.md`, treat `AGENTS.md` as the detailed contract.

Default memory root:

```text
{MEMORY_ROOT}
```

Core startup rule for non-trivial tasks:

1. Read `{MEMORY_ROOT}/knowledge-base/wiki/index.md`.
2. Read `{MEMORY_ROOT}/knowledge-base/wiki/log.md`.
3. Search `{MEMORY_ROOT}/knowledge-base/wiki` with `rg`.
4. Use `memory-signal.py` for durable incoming messages.
5. Never store secrets or raw sensitive logs.

