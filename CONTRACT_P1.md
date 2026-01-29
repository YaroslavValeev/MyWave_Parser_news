# CONTRACT P1 — правила ownership и идемпотентности

Краткие правила для Bot и сайта по листу **raw_feed** (gid=1039755742). Полный контракт — лист **CONTRACT** в Google Sheets и `utils/contract_schema.py`.

## Ownership

| Роль | Кто пишет | Примеры полей |
|------|-----------|----------------|
| **BOT** | Парсер-бот | `id`, `slug`, `source_*`, `raw_*`, `checksum`, `row_number`, `review_queue`, `draft_version`, `ingest_*`, `status` |
| **SITE** | Сайт (бот не перетирает) | `canonical_url`, **`final_version`**, `approved_by`, `approved_at`, `published_at`, `cover_image_url`, `publish_*` |

- **final_version**: ownership **SITE** — финальная версия поста; бот не пишет.
- **canonical_url**: пишет сайт после публикации; бот не перетирает.

## Идемпотентность ingest

1. **Приоритет**: если заполнен **source_item_id** → проверка существования по тройке **(source_type, source_name, source_item_id)**. Если такая запись уже есть в raw_feed — новая строка **не создаётся**.
2. **Fallback**: если **source_item_id** пустой или не найден в проверке выше → дедупликация по **checksum** (повторный checksum не добавляется).

### Правила заполнения source_item_id по источникам

| Источник | source_item_id |
|----------|----------------|
| **RSS** | `entry.id` или `entry.link` (уникальный идентификатор записи) |
| **Telegram** | `message.id` (ID сообщения в канале) |
| **Website** | URL статьи (article_link) |
| **YouTube** | `video_id` (ID видео) |

Повторный прогон одного и того же элемента источника **не создаёт дубль**.

## Review workflow

- Бот при вставке новой строки в raw_feed ставит **review_queue = true**.
- Бот может писать **draft_version** (черновая версия); **final_version** пишет только сайт.
- Сайт читает **review_queue**, **draft_version** и принимает решение о публикации; заполняет **approved_by**, **approved_at** (и при необходимости **final_version**).
- Бот **не перетирает** поля approved_by, approved_at, final_version, canonical_url и прочие SITE-owned поля.

## Версионирование CONTRACT

- **contract_version** задаётся константой `CONTRACT_VERSION` в `utils/contract_schema.py` (сейчас `1.0.0`).
- **Когда поднимать версию (bump):** при добавлении/удалении полей raw_feed, смене ownership поля, изменении validation_rule.
- **Обновление листа:** `ensure_contract_sheet(doc)` вызывается при `init_google_sheets()`; создаёт лист CONTRACT, если нет; обновляет только заголовки и строки контракта, **не изменяет raw_feed** и другие листы.
