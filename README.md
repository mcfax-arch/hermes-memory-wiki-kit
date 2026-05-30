# 📝 Hermes Memory Wiki Kit v3

**Нативный MemoryProvider плагин** для Hermes Agent — постоянная память в виде Markdown wiki с FTS5-поиском, графом ссылок, быстрыми захватами (captures) и автопилотом.

> 🚀 **v3** — link graph (`[[wiki-links]]`), captures, autopilot, health reporting, 9 инструментов.

---

## Быстрый старт

```bash
# 1. Скопировать плагин в директорию плагинов Hermes:
cp -r plugins/memory_wiki ~/AppData/Local/hermes/plugins/   # Windows
# cp -r plugins/memory_wiki ~/.hermes/plugins/              # macOS/Linux

# 2. Активировать через CLI:
hermes memory setup
# → Выбрать "memory_wiki" из списка

# 3. Перезапустить Hermes:
hermes
```

## Для тех, кто обновляется с v2

Плагин имеет авто-миграцию — новые SQLite таблицы (`links`, `captures`, `signals`) создаются автоматически при первой загрузке. После копирования файлов достаточно перезапустить Hermes.

## Как это работает — алгоритмы

### 🔄 Алгоритм вспоминания (Recall)

```
                      ┌──────────────────────┐
                      │  Сообщение пользователя │
                      └──────────┬───────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  PREFETCH (авто)        │
                    │                         │
                    │  1. Берёт query из       │
                    │     сообщения            │
                    │  2. FTS5 поиск по SQLite │
                    │  3. BM25 ранжирование    │
                    │  4. Recency boost:       │
                    │     сегодня  → -2.0      │
                    │     неделя   → -0.5      │
                    │     месяц    → -0.1      │
                    │  5. Tier penalty:        │
                    │     active → 0           │
                    │     warm   → +1          │
                    │     stale  → +5          │
                    │  6. Tag boost если       │
                    │     query совпадает      │
                    │  7. До 3 страниц →       │
                    │     system prompt        │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  АГЕНТ ПОЛУЧАЕТ КОНТЕКСТ │
                    │  без своего ведома       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Агент решает (ручное):  │
                    │                         │
                    │  wiki_search  — уточнить │
                    │  wiki_read    — прочитать│
                    │  wiki_graph   — связи    │
                    │  wiki_tags    — по тегам │
                    │  wiki_health  — здоровье │
                    │  (ничего)     — ответить │
                    └─────────────────────────┘
```

### ✍️ Алгоритм записи (Write)

```
┌─────────── sync_turn (авто, каждый turn) ─────────────────┐
│                                                            │
│  После ответа агента:                                      │
│                                                            │
│  user_content ──► _detect_signals():                       │
│                    ├─ correction (5) "не так, wrong"       │
│                    ├─ decision  (4) "давай, let's"        │
│                    ├─ preference(4) "хочу, prefer"        │
│                    ├─ config    (2) .py .yaml пути         │
│                    └─ url       (1) https://...            │
│                                                            │
│  assistant_content ──► сканирование:                       │
│                    ├─ code blocks → +2 за блок             │
│                    └─ "Note:", "Key:" → +4                 │
│                                                            │
│  Σ score > 8? ──► add_capture() в wiki/_captures/          │
│  Иначе ──► log_signal() в SQLite signals                   │
└────────────────────────────────────────────────────────────┘

┌─────────── on_pre_compress (авто) ────────────────────────┐
│                                                            │
│  Перед сжатием контекста:                                  │
│  ─ Извлечь заметки ("Remember:", "Note:", "Pitfall:")     │
│  ─ Извлечь коррекции ("не так", "wrong")                  │
│  ─ score > 8? → авто-capture                               │
│  ─ Сохранить слепок в _compressed/{session}                │
└────────────────────────────────────────────────────────────┘

┌─────────── on_memory_write (авто) ────────────────────────┐
│                                                            │
│  Когда юзер/агент вызывает built-in memory():              │
│  ─ action=add → добавляет запись                           │
│  ─ action=replace → находит и заменяет                     │
│  ─ Запись зеркалится в preferences.md или environment.md   │
└────────────────────────────────────────────────────────────┘

┌─────────── on_delegation (авто) ──────────────────────────┐
│                                                            │
│  Subagent завершил → сохраняет task+result в _delegations/ │
└────────────────────────────────────────────────────────────┘

┌─────────── on_session_end (авто) ─────────────────────────┐
│                                                            │
│  ─ Записывает сводку сессии в _session-history.md          │
│  ─ Авто-capture сигналов за всю сессию                     │
│  ─ Если turn_count > 3 и были сигналы → add_capture()      │
└────────────────────────────────────────────────────────────┘

┌─────────── Агент решает (ручное) ─────────────────────────┐
│                                                            │
│  wiki_write(name, content)   — осознанное создание страницы│
│  wiki_capture(content, tags) — быстрый захват факта        │
│  memory(action, target, ...) — built-in (зеркалится в wiki)│
└────────────────────────────────────────────────────────────┘

┌─────────── Обслуживание (cron) ───────────────────────────┐
│                                                            │
│  wiki-maintenance.py:                                      │
│  ├─ --decay:   tier по возрасту (30d→warm, 90d→stale)     │
│  ├─ --promote: captures с общими тегами → wiki страницы     │
│  ├─ --health:  orphans, broken links, backlog, score       │
│  └─ --graph:   топ связей, изоляция                        │
└────────────────────────────────────────────────────────────┘
```

### Жизненный цикл

Плагин реализует `MemoryProvider` ABC и встраивается в жизненный цикл Hermes:

| Хук | Что делает |
|---|---|
| `initialize()` | Создаёт wiki-директорию + SQLite FTS5-индекс + авто-индексация существующих .md |
| `system_prompt_block()` | Добавляет описание инструментов в system prompt |
| `prefetch(query)` | Перед каждым turn-ом ищет релевантные страницы (FTS5 + буст по свежести, тегам и tier) |
| `sync_turn(user, assistant)` | **v3: Autopilot** — сканирует сигналы (коррекции, решения, предпочтения), авто-capture при score > 8 |
| `on_memory_write(action, target, content)` | Зеркалирует записи встроенной `memory` в wiki-страницы |
| `on_pre_compress(messages)` | **v3:** Усиленный анализ + авто-capture высоко-скоренных сигналов |
| `on_delegation(task, result)` | Сохраняет результаты субэйджентов |
| `on_session_switch(new_id)` | Корректно обновляет session_id при /resume, /branch |
| `on_session_end(messages)` | Записывает сводку сессии + авто-capture сигналов за сессию |
| `shutdown()` | Закрывает хранилище |

### Инструменты (9)

| Инструмент | Описание | v3 |
|---|---|---|
| `wiki_search(query, limit)` | FTS5-поиск с BM25 + recency/tier/tag boost | ✓ |
| `wiki_read(name)` | Читает страницу wiki по имени | ✓ |
| `wiki_write(name, content, message)` | Создаёт/обновляет страницу (Markdown + YAML frontmatter) | ✓ |
| `wiki_ls(sort)` | Список всех страниц с заголовками и тегами | ✓ |
| `wiki_stats()` | Статистика хранилища | ✓ |
| **`wiki_capture(content, tags)`** | Быстрый захват факта без создания полной страницы | **new** |
| **`wiki_graph(name)`** | Граф связей страницы (кто на кого ссылается) | **new** |
| **`wiki_tags(tag)`** | Просмотр страниц по тегам | **new** |
| **`wiki_health()`** | Health report: orphans, broken links, tiers, backlog, score | **new** |

## Структура на диске

```
{HERMES_HOME}/memory-wiki/
├── wiki/
│   ├── preferences.md          # Зеркало USER.md
│   ├── environment.md          # Зеркало MEMORY.md
│   ├── index.md                # Авто-оглавление
│   ├── _captures/              # v3: Быстрые захваты (*.md файлы)
│   │   └── capture-20250530-120000.md
│   ├── _compressed/            # Слепки сжатия контекста
│   ├── _delegations/           # Результаты субэйджентов
│   ├── _session-history.md     # История сессий
│   └── ...                     # Ваши страницы
├── index.md                    # TOC (авто)
├── log.md                      # Журнал изменений (авто)
├── plugin-config.json          # Конфиг
└── .state/
    ├── search.db               # SQLite FTS5 + links + captures + signals (авто)
```

## Link Graph

Создавайте связи между страницами через `[[wiki-links]]` прямо в Markdown:

```markdown
# Моя страница

Эта концепция связана с [[preferences]] и [[environment]].

Смотри также:
- [[projects/my-app]]
```

Плагин автоматически отслеживает:

- **Outgoing:** какие страницы упоминаются
- **Incoming:** какие страницы ссылаются на эту
- **Orphans:** страницы без единой связи
- **Broken links:** `[[ссылки]]`, ведущие в никуда

Используйте `wiki_graph(name)` для просмотра связей и `wiki_health()` для проверки целостности.

## Autopilot

Автоматическое обнаружение важных сигналов:

| Сигнал | Score | Пример |
|---|---|---|
| Коррекция | 5.0 | "Нет, это не так, надо иначе" |
| Решение | 4.0 | "Давай попробуем Kaspersky" |
| Предпочтение | 4.0 | "Я предпочитаю бесплатные модели" |
| Конфиг/путь | 2.0 | "config.yaml: model.context_length=..." |
| URL | 1.0 | "https://example.com/api" |

При суммарном score > 8 — авто-сохранение в captures. Всё автоматически, без участия пользователя.

## Сравнение: Built-in vs Wiki

| | Built-in (MEMORY.md/USER.md) | Wiki (этот плагин) |
|---|---|---|
| Размер | 2 200 + 1 375 символов | Безлимит (диск) |
| Формат | `§`-разделённые записи | Markdown-файлы |
| Поиск | Нет (вставляется как есть) | FTS5 + link graph |
| Редактирование | Через `memory` tool | Через 9 tools + напрямую в файлах |
| Для чего | Быстрые заметки, поверхностные факты | Глубокое структурированное знание |

Работают вместе. Built-in — для компактных фактов, всегда в system prompt. Wiki — для глубины, связей и истории.

## Инструменты обслуживания (CLI)

В `tools/` — standalone Python-скрипты, работающие без Hermes:

```bash
# Быстрый захват факта из командной строки
python tools/wiki-capture.py "KPM service restarts on boot" --tags windows kpm

# Список непромоученных захватов
python tools/wiki-capture.py --list --unpromoted

# Полное обслуживание (decay + promote captures + health)
python tools/wiki-maintenance.py

# Health report
python tools/wiki-maintenance.py --health

# Link graph report
python tools/wiki-maintenance.py --graph

# Decay pass только
python tools/wiki-maintenance.py --decay
```

Можно поставить в cron / Task Scheduler для ежедневного обслуживания.

## Структура плагина

```
plugins/memory_wiki/
├── __init__.py       # MemoryWikiProvider(MemoryProvider) — жизненный цикл + 9 tools
├── store.py          # WikiStore — Markdown CRUD + SQLite FTS5 + links + captures
├── plugin.yaml       # Метаданные (v3.0.0)
└── README.md         # Документация плагина
```

Ноль внешних зависимостей. Использует: `os`, `re`, `json`, `sqlite3`, `threading`, `pathlib`, `logging`.

## Конфигурация

Через `hermes memory setup`:

- `wiki_root` — путь (по умолч.: `{HERMES_HOME}/memory-wiki`)
- `prefetch_limit` — макс. страниц на turn (по умолч.: 3)
- `auto_capture` — зеркалирование built-in памяти + autopilot (по умолч.: true)

Или через `plugin-config.json` в корне wiki.

## Требования

- Hermes Agent (любая версия с `MemoryProvider` ABC, 2025+)
- Python 3.10+
- SQLite3 (встроен в Python)

## Changelog

См. [CHANGELOG.md](CHANGELOG.md) — от v1 до v3.

## Лицензия

MIT
