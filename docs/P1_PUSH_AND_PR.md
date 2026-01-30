# P1: Push ветки и открытие PR (обязательно)

## Шаг 1: Ветка и коммит

Из корня репозитория:

```bash
# Создать ветку P1 (от текущего HEAD; при необходимости сначала: git fetch origin && git checkout main)
git checkout -b p1-contract-idempotency-review

# Добавить только P1-артефакты и изменённые файлы
git add utils/contract_schema.py
git add "utils/import asyncio.py"
git add test_p1_integration.py
git add CONTRACT_P1.md
git add docs/P1_STATUS.md
git add docs/CONTRACT_SNAPSHOT.md
git add docs/P1_RUN_RESULTS.md
git add docs/P1_PUSH_AND_PR.md
git add P1_PR_DESCRIPTION.md

git status
git commit -m "P1: CONTRACT-лист, идемпотентность source_item_id→checksum, review_queue TRUE, ownership, тесты"
```

## Шаг 2: Push (обычный или «лёгкий»)

### Вариант A: Обычный push

```bash
git push -u origin p1-contract-idempotency-review
```

Если push падает по таймауту из‑за большого объёма истории (как с P0):

### Вариант B: Лёгкий push (как с P0)

```bash
# Ветка только с P1-коммитом, без тяжёлой истории
git fetch origin main
git checkout -b p1-push-only origin/main
git cherry-pick <hash_P1_коммита>
git push -u origin p1-push-only
```

Далее на GitHub: **Compare & pull request** `p1-push-only` → `main`. После мержа при необходимости удалить ветку `p1-contract-idempotency-review` локально.

## Шаг 3: Открыть PR

1. Перейти в репозиторий на GitHub.
2. Создать **Pull Request** из ветки `p1-contract-idempotency-review` (или `p1-push-only`) в **main**.
3. **Заголовок:** `P1: CONTRACT, идемпотентность, review workflow`
4. **Описание:** вставить содержимое файла **P1_PR_DESCRIPTION.md** (или см. ниже).

### Краткое описание для PR

- **CONTRACT-лист:** лист CONTRACT в Sheets, автогенерация из contract_schema, версионирование, ensure без побочных эффектов на raw_feed.
- **Идемпотентность:** source_item_id (приоритет) → checksum (fallback); повторный прогон не создаёт дубль.
- **Review workflow:** review_queue по умолчанию TRUE (булево в Sheets); бот не перетирает SITE-owned при update.
- **Тесты/DoD:** test_p1_integration.py; результаты прогона зафиксированы в docs/P1_RUN_RESULTS.md.
