# P1: CONTRACT, идемпотентность, review workflow

## Что сделано

### CONTRACT-лист
- Лист **CONTRACT** в Google Sheets — единый источник правды по схеме raw_feed, ownership и валидации.
- Автогенерация из `utils/contract_schema.py` (RAW_FEED_COLUMNS + owner, required_for, validation_rule, contract_version).
- При инициализации (`init_google_sheets`) вызывается `ensure_contract_sheet(doc)`: создаёт лист, если нет; обновляет только CONTRACT, не трогает raw_feed.
- Версия контракта зафиксирована константой `CONTRACT_VERSION`; правила bump описаны в коде (добавление/удаление полей, смена ownership, изменение validation_rule).

### Идемпотентность ingest (source_item_id → checksum)
- В `save_to_sheet` для raw_feed: приоритет — проверка по **(source_type, source_name, source_item_id)**; если запись уже есть — новая строка не создаётся.
- Fallback — дедупликация по **checksum** (повторный checksum не добавляется).
- Правила заполнения source_item_id по источникам (RSS/Telegram/Website/YouTube) зафиксированы в CONTRACT_P1.md.

### Review workflow
- Для новых строк raw_feed при вставке выставляется **review_queue = TRUE** (булево в Sheets, не строка `"true"`) — корректное чтение сайтом.
- Бот не перетирает SITE-owned поля: в **update_sheet_row** для raw_feed все поля из `SITE_OWNED_FIELDS` (canonical_url, publish_*, approved_*, final_version и др.) пропускаются при update.

### Тесты и DoD
- **test_p1_integration.py**: проверка листа CONTRACT, вставка с review_queue без approve, идемпотентность по source_item_id, стабильность row_number и заголовков.
- DoD: CONTRACT покрывает критичные поля; повторный прогон не создаёт дубль; review_queue по умолчанию TRUE для новых записей; тесты воспроизводимы.

## Артефакты

- `docs/P1_STATUS.md` — статус и DoD.
- `CONTRACT_P1.md` — правила ownership и идемпотентности.
- `docs/CONTRACT_SNAPSHOT.md` — снимок контракта.
- `docs/P1_RUN_RESULTS.md` — шаблон для фиксации результатов прогона на реальной таблице.

## Как проверить

```bash
python test_p1_integration.py
```

После прогона заполнить `docs/P1_RUN_RESULTS.md` (ссылка на строку с review_queue=TRUE, подтверждение дедупликации, наличие листа CONTRACT).
