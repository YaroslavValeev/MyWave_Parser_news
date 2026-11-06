# План: сбор авторов сообщений из обычных Telegram-групп

Связано с: существующий `channel_commenters` / `/collect_commenters`, Stage 7 Contacts (`CONTENT_ENGINE_CANON.md`), `services/contact_consent.py`.

## 1. Краткое резюме запроса

Нужен **аккуратный** сбор аудитории из вейксерф-групп: не полный roster участников, а только **авторы сообщений** (кто реально писал в чат). Полный `GetParticipants` / `iter_participants` **не делаем** (приватность, FloodWait, риск бана, низкая ценность «молчащих»).

## 2. Design Plan

### 2.1 Цель MVP

| Цель | Не цель |
|------|---------|
| Уникальные `user_id` / `@username` авторов сообщений в группах | Выгрузка всех участников группы |
| Идемпотентная запись + sync в Sheets | Массовый outreach / spam |
| Малый лимит сообщений, задержки, checkpoint | Глубокий полный history dump без контроля |
| Feature-flag off по умолчанию | Автозапуск на всех telegram-источниках новостей |

### 2.2 Границы продукта

```
[Группа / супергруппа]
        │  Telethon user-session (уже есть)
        ▼
 iter_messages(limit=N)  ──►  уникальные авторы
        │
        ▼
 SQLite (audience store)  ──►  лист user_messages (или отдельный лист)
        │
        ▼
 Consent gate  ──►  только ручной / consented outreach (уже запрещён mass blast)
```

**Отличие от текущего engagement:**

| | `/collect_commenters` | Новый сбор (группы) |
|--|----------------------|---------------------|
| Объект | Канал + linked discussion | Обычная группа / megagroup |
| Сигнал | Комментарий под постом | Любое сообщение в чате |
| Сейчас | Реализовано | Нет |

### 2.3 Источники групп

- Не смешивать с новостными `telegram`-каналами из `storage/sources.py` без явной метки.
- Предпочтительный MVP: **явный список URL групп** (env / отдельный конфиг / команда с URL).
- Фаза 2 (опционально): тип источника `telegram_group` или флаг `audience=true` у source — только после стабильного MVP.

**Допущение:** аккаунт user-сессии уже состоит в целевых группах (или группы публичные и join возможен). Приватные invite-only без членства — skip + лог.

### 2.4 Модель данных (минимальная)

**Вариант A (рекомендуется для MVP):** расширить контракт `user_messages` / переиспользовать таблицу, близкую к `channel_commenters`, с новым `message_type`:

- `message_type = group_message_author` (или `group_chat_message`)
- `related_id` = `group_url` или `chat_id`
- уникальность автора: `author_id = sha256(group_url|user_id)` — **один ряд на пользователя в группе**, не каждое сообщение

**Поля записи (канон MVP):**

| Поле | Обязательно | Смысл |
|------|-------------|--------|
| `author_id` | да | стабильный id для upsert |
| `group_url` / `chat_id` | да | источник |
| `group_title` | нет | человекочитаемое имя |
| `user_id` | да* | TG user id (*если скрыт — skip или только username) |
| `user_name` | нет | @username или display name |
| `last_message_id` | да | для checkpoint / идемпотентности |
| `last_message_at` | да | ISO UTC |
| `message_snippet` | нет | до 200–500 символов, без лишнего PII в логах |
| `messages_seen` | нет | счётчик (инкремент при повторных проходах) |
| `synced_to_sheet` | да | как у commenters |
| `source_name` | нет | ярлык |

**Не хранить в логах:** полный текст переписки, телефоны, сырой dump сообщений.

### 2.5 Алгоритм сбора (безопасный)

1. Resolve entity по URL / `@username` / `t.me/+...` (если сессия уже в группе).
2. Проверка: это **чат/мегагруппа**, не broadcast-канал. Каналы → оставить на `/collect_commenters`.
3. `iter_messages(entity, limit=GROUP_AUTHORS_MESSAGE_LIMIT)` (default **100**, не тысячи).
4. Для каждого сообщения:
   - skip `out` (свои), skip service messages, skip ботов (опциональный флаг);
   - взять sender → `user_id`, `username` / display name;
   - upsert по `author_id` (обновить `last_message_*`, инкремент `messages_seen`).
5. Пауза `GROUP_AUTHORS_DELAY_SECONDS` между сообщениями/батчами; на `FloodWait` — sleep до `min(seconds, 120)`, max 3 retry.
6. **Checkpoint:** сохранять `min_message_id` / `last_scanned_at` per group, чтобы следующий прогон шёл **только новее** (или `offset_id`), а не весь limit заново.
7. После сбора — sync в Sheets (как `user_messages_sync`), mark synced.

### 2.6 Feature flags / env

```env
GROUP_AUTHORS_COLLECT_ENABLED=false
GROUP_AUTHORS_MESSAGE_LIMIT=100
GROUP_AUTHORS_DELAY_SECONDS=0.1
GROUP_AUTHORS_CHANNELS_CHUNK=1
GROUP_AUTHORS_SKIP_BOTS=true
GROUP_AUTHORS_CRON_HOUR=5
GROUP_AUTHORS_CRON_MINUTE=0
# MVP: явный список через запятую
GROUP_AUTHORS_URLS=https://t.me/group1,https://t.me/group2
```

Cron — только если `GROUP_AUTHORS_COLLECT_ENABLED=true` и список URL не пуст. По умолчанию **выкл**.

### 2.7 Команды бота (оператор)

| Команда | Поведение |
|---------|-----------|
| `/collect_group_authors` | Прогон по `GROUP_AUTHORS_URLS` (chunk) |
| `/collect_group_authors <url>` | Одна группа |
| `/stats` | Добавить счётчик «Авторы групп» (отдельно от commenters) |

Только `is_bot_operator`. Личка only — как остальные редакторские команды.

### 2.8 Consent / anti-spam

Переиспользовать `services/contact_consent.py`:

- запись = **audience intelligence**, не база для рассылки;
- auto-outreach без `has_explicit_consent` — запрещён;
- `channel=blast|mass|spam` — всегда deny;
- в UI/доках явно: «собрали активных авторов, не спамим».

### 2.9 Порядок реализации (без большой ломки)

| Шаг | Что | Rollback |
|-----|-----|----------|
| 0 | Этот план + согласование Owner | — |
| 1 | Контракт: `utils/group_authors_contract.py` + колонки Sheets | не писать в sheet |
| 2 | Миграция SQLite `group_authors` (или расширение существующей таблицы) | drop table / flag off |
| 3 | Collector `collectors/group_authors.py` (только iter_messages) | не вызывать |
| 4 | Service `services/group_authors_engagement.py` + sync | flag off |
| 5 | Команда `/collect_group_authors` | убрать handler |
| 6 | Опционально cron в scheduler | `GROUP_AUTHORS_COLLECT_ENABLED=false` |
| 7 | Тесты offline (моки Telethon) + dry-run на 1 группе | — |

**Не трогать** в первой итерации: `ContactsParser`, news `/parse`, raw_feed, publication.

## 3. Реализация в коде (Cursor-ready outline)

Новые / затронутые файлы:

```
docs/GROUP_MESSAGE_AUTHORS_PLAN.md          # этот план
utils/group_authors_contract.py             # id, row mapping, message_type
storage/migrations/00X_group_authors.sql
storage/repository.py                       # upsert / count / unsynced
storage/data.py                             # thin wrappers
collectors/group_authors.py                 # Telethon iter_messages → records
services/group_authors_engagement.py        # orchestration + sheet sync
services/user_messages_sync.py              # переиспользовать или тонкий twin
config/settings.py                          # env flags
telegram_bot/router.py                      # /collect_group_authors
services/scheduler.py / core/scheduler.py   # optional cron
tests/test_group_authors_*.py
```

Псевдокод коллектора:

```python
async def collect_group_authors(client, group_url: str, *, limit: int, delay: float):
    entity = await client.get_entity(normalize_group_url(group_url))
    if is_broadcast_channel(entity):
        return [], skip_reason="use_collect_commenters"
    by_user: dict[str, dict] = {}
    async for msg in client.iter_messages(entity, limit=limit, min_id=checkpoint):
        sender = await msg.get_sender()  # или msg.sender при prefetch
        if skip_sender(sender):
            continue
        uid, uname = sender_fields(sender)
        key = make_author_id(group_url=url, user_id=uid)
        # upsert in-memory then DB
        ...
        if delay:
            await asyncio.sleep(delay)
    return list(by_user.values()), stats
```

## 4. Тесты и проверка

- Unit: `make_author_id` стабилен; dedup двух сообщений одного user → 1 запись.
- Unit: broadcast channel → skip.
- Unit: FloodWait retry capped.
- Integration (mock client): 3 сообщения / 2 автора → `saved_db=2`.
- Ручной dry-run: 1 публичная/доступная wakesurf-группа, `limit=50`, сверка `/stats` и листа Sheets.
- Регрессия: `/collect_commenters` и `/parse` без изменений поведения.

**Критерий готовности MVP:** оператор одной командой собирает авторов из заданной группы; данные в SQLite + Sheets; flag по умолчанию off; consent не ослаблен.

## 5. Миграция и деплой

1. Залить код на VPS **без** включения cron.
2. Добавить env flags (`false`).
3. Создать/проверить лист Sheets (те же колонки `user_messages` + новый `message_type`, либо отдельный лист `group_authors` — решение Owner на шаге 0).
4. Ручной прогон `/collect_group_authors <url>` на 1 группе.
5. Только после успешного dry-run — расширять `GROUP_AUTHORS_URLS` и при необходимости включать cron.

Rollback: `GROUP_AUTHORS_COLLECT_ENABLED=false`, убрать URLs, команда no-op / не регистрировать job.

## 6. Риски и допущения

| Риск | Митигация |
|------|-----------|
| FloodWait / бан user-аккаунта | малый limit, delay, chunk=1, no participants API |
| Нет доступа к приватной группе | skip + note в ответе команды |
| Путаница с новостными каналами | явный список URL / тип; skip broadcast |
| Дубли с `channel_commenters` | разные `message_type` / таблица; не мержить blindly |
| PII в логах | логировать `group_url`, counts, `job_id`; не текст/телефоны |
| Ожидание «всех участников» | явно документировать: только писавшие |
| Sheets schema drift | validate headers как в `channel_commenters_contract` |
| Юридический / ToS Telegram | только группы, куда аккаунт допущен; audience intel, не scraping на продажу |

**Допущения:**

1. Owner согласен на audience-сбор только **активных авторов**, не full members.
2. User-сессия Telethon уже валидна (как для engagement).
3. На первом этапе достаточно env-списка групп, без правки всего каталога sources.
4. Отдельная таблица `group_authors` предпочтительнее перегрузки `channel_commenters` семантикой «не канал» — меньше регрессий; sync может всё равно идти в `user_messages` с другим `message_type`.

---

## Owner decisions (зафиксировано 2026-11-06)

1. **Sheets:** тот же лист `user_messages`, `message_type=group_message_author`.
2. **Группы MVP (chat id):**  
   `3099511486, 1701957243, 1606459352, 1791775467, 2441251847, 1738625669, 2147018906, 1124621462`  
   (в Telethon резолв как `-100{id}` для megagroup/channel).
3. **Cron:** сразу (`GROUP_AUTHORS_COLLECT_ENABLED=true`, job в ~05:00).

## Статус реализации

MVP в коде: миграция `005_audience_tables.sql`, collector/service, `/collect_group_authors`, cron, sync в `user_messages`.  
User-сессия должна состоять в этих группах; иначе будет `skipped_no_access`.
