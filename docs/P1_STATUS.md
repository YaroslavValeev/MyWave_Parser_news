# P1 — статус (Contract, Idempotency, Review Workflow)

## Цель P1 (на стороне Bot)

1. CONTRACT-лист как единый источник правды по схеме/ownership/валидации.
2. Идемпотентность ingest: **source_item_id** (приоритет) → **checksum** (fallback).
3. Review workflow: поля **review_queue**, **approved_by** / **approved_at**, **draft_version**; бот не перетирает publish-поля сайта.
4. P1 интеграционные проверки.

---

## DoD и статус

| Задача | DoD | Статус |
|--------|-----|--------|
| **CONTRACT-лист** | Лист CONTRACT создан и заполнен из RAW_FEED_COLUMNS + contract_schema; 100% критичных полей; версионирование | ✅ Реализовано: `ensure_contract_sheet(doc)` при init, лист CONTRACT в SHEET_COLUMNS |
| **Идемпотентность** | Повторный прогон одного и того же элемента источника не создаёт дубль | ✅ В `save_to_sheet`: проверка (source_type, source_name, source_item_id); fallback по checksum |
| **Review workflow** | Бот пишет review_queue/draft_version; не перетирает approved_*, final_version | ✅ При вставке в raw_feed выставляется review_queue=true; SITE_OWNED_FIELDS в contract_schema |
| **final_version** | Ownership SITE — бот не пишет | ✅ Зафиксировано в contract_schema (SITE_OWNED_FIELDS) и CONTRACT_P1.md |
| **P1 интеграционные проверки** | Скрипт/тест: CONTRACT, review_queue без approve, идемпотентность, row_number/headers | ✅ `test_p1_integration.py` |

---

## Артефакты

- **docs/P1_STATUS.md** — этот файл.
- **CONTRACT_P1.md** — краткие правила ownership и идемпотентности.
- **docs/CONTRACT_SNAPSHOT.md** — снимок контракта для ревью (список полей и owner).
- **test_p1_integration.py** — воспроизводимый тестовый прогон P1.

---

## Запуск P1 проверок

```bash
python test_p1_integration.py
```

Требуется: `.env`, `credentials.json`, доступ к Google Sheet с raw_feed и (после первого запуска бота) листом CONTRACT.

---

## P1.1 (желательно)

- **Self-healing headers**: при расширении CONTRACT/RAW_FEED_COLUMNS — `ensure_sheet_headers` уже добавляет недостающие колонки в raw_feed без падения.
- **Экспорт contract snapshot в md** — см. docs/CONTRACT_SNAPSHOT.md.
