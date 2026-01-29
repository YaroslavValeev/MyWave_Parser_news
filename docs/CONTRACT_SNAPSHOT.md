# CONTRACT Snapshot (raw_feed)

Снимок контракта для ревью: поле → owner. Полные правила — лист **CONTRACT** в Google Sheets и **CONTRACT_P1.md**.

**Версия контракта:** 1.0.0 (P0+P1)

## Ownership (кратко)

- **BOT** — пишет бот при ingest/обработке.
- **SITE** — пишет сайт; бот не перетирает.
- **BOTH** — оба могут читать/писать по контракту.

## Критичные поля P0+P1

| field_name | owner | примечание |
|------------|-------|------------|
| row_number | BOT | обязателен при вставке; должен совпадать с номером строки |
| checksum | BOT | SHA256; fallback идемпотентности |
| source_item_id | BOT | приоритет идемпотентности в рамках (source_type, source_name) |
| review_queue | BOT | true для новых записей; сайт читает для ревью |
| draft_version | BOT | опционально |
| approved_by, approved_at | SITE | бот не перетирает |
| final_version | SITE | финальная версия поста; бот не пишет |
| canonical_url | SITE | пишет сайт после публикации |

Полный список полей и validation_rule — в листе CONTRACT (колонки: field_name, owner, required_for, validation_rule, default_value, notes, contract_version, updated_at).
