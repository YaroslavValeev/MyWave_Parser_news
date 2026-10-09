# Content Engine — последовательность миграции

Связано с: [`CONTENT_ENGINE_CANON.md`](../architecture/CONTENT_ENGINE_CANON.md)

## Порядок (строго)

| # | Этап | Зависимости | Rollback point | Owner GO |
|---|------|-------------|----------------|----------|
| 1 | Telemetry / source health | текущий collect_report | отключить запись metrics, оставить collect | нет (dev) |
|   | **статус: код готов** (`source_health`, health/alerts, scaffolds 3–8) | | | |
| 2 | Controlled E2E | telemetry + media contract | `DEPLOY_BY_SHA` + ROLLBACK_SHA | **да** (prod post) |
|   | **статус: trace-скрипт готов; prod доказательство — Owner GO** | | | |
| 3 | Semantic dedup | E2E proven | feature-flag `SEMANTIC_DEDUP=0` | нет |
| 4 | Editorial integrity fields | schema CONTRACT | не писать новые поля | да, если sheet schema |
| 5 | Media production harden | E2E + editorial | retry off / status=missing | по media |
| 6 | Knowledge export contract | E2E + archive | не вызывать KB API | да (KB) |
| 7 | Contacts consent policy | contacts parser | parser off | да (PII) |
| 8 | Multi-channel adapters | Telegram+Blog stable | adapter disable | да (каналы) |

## Совместимость

- Не ломать P1 idempotency (`source_item_id` / `checksum`).
- Не перетирать SITE_OWNED поля.
- Ветка `feat/blog-editorial-media-pipeline` → merge в release только после Этапа 2 DoD на staging/controlled.

## Автоматизация Cursor vs ручной контроль

| Можно агентом | Только Owner |
|---------------|--------------|
| telemetry schema + tests | prod deploy SHA |
| E2E checklist scripts | controlled publish |
| semantic dedup prototype + flag | KB write to shared store |
| media retry / MIME guards | credential rotation |
| consent policy stubs | audience outreach |

## Критерий старта кодинга Этапа N+1

Чеклист DoD этапа N закрыт в каноне; QA Agent подтверждает regression green.
