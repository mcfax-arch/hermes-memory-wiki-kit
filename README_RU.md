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

## Как это работает

Плагин реализует `MemoryProvider` ABC и встраивается в жизненный цикл Hermes.

### Инструменты (9)

| Инструмент | Описание |
|---|---|
| `wiki_search(query, limit)` | FTS5-поиск с BM25 + recency/tier/tag boost |
| `wiki_read(name)` | Читает страницу wiki |
| `wiki_write(name, content, message)` | Создаёт/обновляет страницу |
| `wiki_capture(content, tags)` | **NEW** Быстрый захват факта |
| `wiki_graph(name)` | **NEW** Граф связей (кто на кого ссылается) |
| `wiki_tags(tag)` | **NEW** Просмотр по тегам |
| `wiki_ls(sort)` | Список страниц |
| `wiki_stats()` | Статистика |
| `wiki_health()` | **NEW** Health report |

### Link Graph

Создавайте связи через `[[wiki-links]]` в Markdown. Плагин отслеживает outgoing/incoming связи, находит orphan-страницы и broken links.

### Autopilot

Автоматическое обнаружение сигналов (коррекции, решения, предпочтения). При score > 8 — авто-capture. Всё без участия пользователя.

## Инструменты обслуживания (CLI)

```bash
# Быстрый захват
python tools/wiki-capture.py "факт" --tags "tag1 tag2"

# Полное обслуживание
python tools/wiki-maintenance.py

# Health report
python tools/wiki-maintenance.py --health

# Link graph
python tools/wiki-maintenance.py --graph
```

## Структура на диске

```
{HERMES_HOME}/memory-wiki/
├── wiki/
│   ├── preferences.md       # Зеркало USER.md
│   ├── environment.md       # Зеркало MEMORY.md
│   ├── _captures/           # Быстрые захваты
│   ├── index.md             # Авто-оглавление
│   └── ...                  # Ваши страницы
├── index.md                 # TOC (авто)
├── log.md                   # Журнал (авто)
└── .state/
    └── search.db            # SQLite FTS5 + links + captures
```

## Требования

- Hermes Agent 2025+
- Python 3.10+
- SQLite3 (встроен в Python)

## Лицензия

MIT
