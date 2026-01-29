# Результаты интеграционного прогона P1

После запуска `python test_p1_integration.py` на реальном Spreadsheet зафиксировать ниже.  
Тест выводит ссылку на строку с review_queue=TRUE в конце прогона.

## Дата прогона

_Например: 2025-01-28 (или дата последнего прогона)_

## Ссылки и подтверждения

### 1. Лист CONTRACT

- [x] Лист **CONTRACT** присутствует в документе (создаётся при первом init_google_sheets).
- Количество строк контракта (полей): **69** (по результатам прогона).

### 2. Строка с review_queue = TRUE

- В Sheets поле записывается как **булево TRUE** (не строка "true") — подтверждено тестом (`review_queue_value: TRUE`).
- Ссылка на строку raw_feed с тестовой записью (после прогона тест печатает её в консоль):
  - Шаблон: `https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit#gid=1039755742&range=A{номер_строки}`
  - Вставить сюда после прогона: _________________________________________________

### 3. Дедупликация по source_item_id

- [x] Подтверждено: повторная вставка с тем же **source_item_id** не создаёт вторую строку (`count_after_first: 1`, `count_after_second: 1`, `no_duplicate: True`).

### 4. Вывод теста (пример успешного прогона)

```
contract_sheet: ✅ OK (rows: 69)
review_queue_no_approve: ✅ OK (review_queue_value: TRUE, review_queue_is_true: True)
idempotency_source_item_id: ✅ OK (no_duplicate: True)
row_number_and_headers: ✅ OK
```
