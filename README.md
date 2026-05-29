# 📝 Hermes Memory Wiki Kit

**Нативный MemoryProvider плагин** для Hermes Agent — постоянная память в виде Markdown wiki с FTS5-поиском, авто-префетчем и синхронизацией со встроенной памятью.

> 🚀 **Что изменилось**: старый подход на SKILL.md + AGENTS.md заменён на настоящий [MemoryProvider плагин](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin). Работает через `hermes memory setup`, не требует ручной загрузки скилла.

---

## Быстрый старт

```bash
# 1. Скопировать плагин в директорию плагинов Hermes:
cp -r plugins/memory_wiki ~/AppData/Local/hermes/plugins/   # Windows
# cp -r plugins/memory_wiki ~/.hermes/plugins/              # macOS/Linux

# 2. Активировать через CLI:
hermes memory setup
# → Выбрать "memory_wiki" из списка

# 3. Начать новую сессию:
hermes
```

Всё. Никаких API-ключей, конфигов или внешних зависимостей.

## Как это работает

Плагин реализует `MemoryProvider` ABC и встраивается в жизненный цикл Hermes:

| Хук | Что делает |
|---|---|
|| `initialize()` | Создаёт wiki-директорию + SQLite FTS5-индекс + авто-индексация существующих .md |
|| `system_prompt_block()` | Добавляет описание инструментов в system prompt |
|| `prefetch(query)` | Перед каждым turn-ом ищет релевантные страницы (FTS5 + буст по свежести и тегам) |
|| `on_memory_write(action, target, content)` | Зеркалирует записи встроенной `memory` в wiki-страницы |
|| `on_pre_compress(messages)` | Сохраняет заметки и коррекции перед сжатием контекста |
|| `on_delegation(task, result)` | Сохраняет результаты субэйджентов |
|| `on_session_switch(new_id)` | Корректно обновляет session_id при /resume, /branch |
|| `on_session_end(messages)` | Записывает сводку сессии в `_session-history.md` |
|| `shutdown()` | Закрывает хранилище |

### Инструменты

| Инструмент | Описание |
|---|---|
| `wiki_search(query, limit)` | FTS5-поиск с BM25-ранжированием, возвращает сниппеты |
| `wiki_read(name)` | Читает страницу wiki по имени |
| `wiki_write(name, content, message)` | Создаёт/обновляет страницу (Markdown + YAML frontmatter) |
| `wiki_ls(sort)` | Список всех страниц с заголовками и тегами |
| `wiki_stats()` | Статистика хранилища |

## Структура на диске

```
{HERMES_HOME}/memory-wiki/
├── wiki/
│   ├── preferences.md      # Зеркало USER.md
│   ├── environment.md      # Зеркало MEMORY.md
│   ├── index.md            # Авто-оглавление
│   └── ...                 # Ваши страницы
├── index.md                # TOC (авто)
├── log.md                  # Журнал изменений (авто)
├── plugin-config.json      # Конфиг
└── .state/
    ├── search.db           # SQLite FTS5-индекс (авто)
    └── link_graph.json     # Граф ссылок (опционально)
```

## Сравнение: Built-in vs Wiki

| | Built-in (MEMORY.md/USER.md) | Wiki (этот плагин) |
|---|---|---|
| Размер | 2 200 + 1 375 символов | Безлимит (диск) |
| Формат | `§`-разделённые записи | Markdown-файлы |
| Поиск | Нет (вставляется как есть) | FTS5-полнотекстовый поиск |
| Редактирование | Через `memory` tool | Через tools + напрямую в файлах |
| Для чего | Быстрые заметки, поверхностные факты | Глубокое структурированное знание |

Работают вместе. Built-in — для компактных фактов, которые всегда в system prompt. Wiki — для глубины.

## Инструменты обслуживания

В `tools/` — standalone Python-скрипты:

```bash
# Линтер: битые ссылки, устаревшие страницы
python tools/lint_wiki.py wiki/

# Граф ссылок между страницами
python tools/memory-graph.py --memory-root ~/.hermes/memory-wiki

# Автопилот: все задачи обслуживания
python tools/memory-autopilot.py --memory-root ~/.hermes/memory-wiki
```

Работают без Hermes. Можно в cron/Task Scheduler.

## Структура плагина

```
plugins/memory_wiki/
├── __init__.py       # MemoryWikiProvider(MemoryProvider) — жизненный цикл + tools
├── store.py          # WikiStore — Markdown CRUD + SQLite FTS5
├── plugin.yaml       # Метаданные для обнаружения плагина
└── README.md         # Документация плагина
```

Ноль зависимостей. Использует: `os`, `re`, `json`, `sqlite3`, `threading`, `pathlib`, `logging`.

## Конфигурация

Через `hermes memory setup`:

- `wiki_root` — путь (по умолч.: `{HERMES_HOME}/memory-wiki`)
- `prefetch_limit` — макс. страниц на turn (по умолч.: 3)
- `auto_capture` — зеркалирование built-in памяти (по умолч.: true)

Или через `plugin-config.json` в корне wiki.

## Требования

- Hermes Agent (любая версия с `MemoryProvider` ABC, 2025+)
- Python 3.10+
- SQLite3 (встроен в Python)

## Как перенестись со старого Kit

Если использовали старый подход с `{MEMORY_ROOT}`:

1. Переместите существующие wiki-страницы в `{HERMES_HOME}/memory-wiki/wiki/`
2. Запустите `python tools/lint_wiki.py` для проверки
3. Удалите старый `{MEMORY_ROOT}`
4. Плагин дальше всё делает сам

## Лицензия

MIT
