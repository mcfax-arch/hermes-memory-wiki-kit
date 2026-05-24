External long-term memory lives at `{MEMORY_ROOT}`.

For non-trivial tasks, project work, source analysis, architecture decisions, or "do you remember" questions:
1. Read `{MEMORY_ROOT}/AGENTS.md`.
2. Read `{MEMORY_ROOT}/knowledge-base/wiki/index.md`.
3. Read `{MEMORY_ROOT}/knowledge-base/wiki/log.md`.
4. Search `{MEMORY_ROOT}/knowledge-base/wiki` with `rg`; use the platform's text search if `rg` is unavailable.
5. Open only relevant pages. Do not load the whole tree.

Use always-on capture gate for durable messages:
`python "{MEMORY_ROOT}/knowledge-base/tools/memory-signal.py" --text "<message>" --project <slug> --source conversation --memory-root "{MEMORY_ROOT}"`

After meaningful work, capture:
`python "{MEMORY_ROOT}/knowledge-base/tools/memory-write.py" "What changed" --project <slug> --why "Why it matters" --context "file:line" --details "Important details" --memory-root "{MEMORY_ROOT}"`

Never store secrets, tokens, cookies, passwords, OAuth URLs, private keys, or raw sensitive logs.

