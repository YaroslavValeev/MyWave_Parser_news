# MyWave Content Engine — канон (ParserNews)

**Статус:** принятый рабочий канон  
**Ветка разработки:** `feat/blog-editorial-media-pipeline`  
**Источник схемы raw_feed:** Google Sheet CONTRACT / `RAW_FEED_COLUMNS`  
**Главный принцип:** не строить новые AI-функции поверх недоказанного pipeline.

---

## 1. Цель

Довести ParserNews от работающего новостного pipeline с ручным editorial gate до полноценного **MyWave Content Engine**:

```
collect → measure → normalize → semantic dedup → enrich → editorial
  → media → publish → archive → knowledge extraction → analytics
```

Доказательство успеха — не «бот живой», а **один материал** с сохранением происхождения от источника до публикации и архива.

---

## 2. Роли Subagents (логические, не отдельные процессы)

| Agent | Слой | Ответственность |
|-------|------|-----------------|
| Source Agent | collect | источники, расписание, доступность |
| Ingestion Agent | ingest | raw → normalize, checksum / source_item_id |
| NLP Agent | enrich | summary, tags, NE; **не** подменяет Owner commentary |
| Editorial Agent | policy gate | review_queue, approve, запрет auto-publish |
| Media Agent | media | MIME, URL safety, upload retry, provenance, thumbnails |
| Publishing Agent | publish | adapters: Telegram, Blog, later site/social |
| Reliability Agent | measure | telemetry, alerts, health ≠ content health |
| Knowledge Agent | KB (после E2E) | сущности и связи → общая KB MyWave |
| Contact/Community Agent | audience | consent, источник, интерес, история; без спама |
| QA Agent | quality | контракты, E2E, regression gates |

Граница: Agents = policy/review; runtime collect/publish остаётся в ParserNews services. Не смешивать product UI и orchestration в один бесформенный слой.

---

## 3. Сквозной ID (обязателен с Этапа 2)

Один материал = один сквозной идентификатор на всём пути.

| Уровень | Поле / ключ | Назначение |
|---------|-------------|------------|
| Source item | `source_type` + `source_name` + `source_item_id` | идемпотентность ingest (уже есть, P1) |
| Content object | `id` (SQLite) / `news_id` / `raw_id` | внутренний сквозной ID |
| Integrity | `checksum` | fallback dedup |
| Event cluster | `event_id` (новое, Этап 3) | semantic/event-level dedup |
| Publish | `slug`, channel message ids, `canonical_url` | каналы и архив |

Правило: повторный collect **не** создаёт дубль объекта. Semantic-дубль сливается в cluster, не плодит публикации.

---

## 4. Разделение фактов и мнения (Этап 4)

| Слой | Где хранить | Кто пишет |
|------|-------------|-----------|
| Исходный факт | `raw_title`, `raw_content`, `raw_html`, `source_*` | Ingestion |
| Авто summary | NLP / `summary` | NLP Agent |
| Owner commentary | `expert_opinion` / author_notes | Owner via Editorial |
| Interpretation | отдельное поле или пометка в NLP meta | только явно; не смешивать с фактом |

**Политика:** никакая публикация без editorial policy / Owner gate. Уже частично: publish блокируется без owner comment.

---

## 5. Что уже есть vs пробелы (аудит)

| Область | Есть | Нет / не доказано |
|---------|------|-------------------|
| Collect / parse / normalize | collectors, raw_feed | — |
| Technical dedup | source_item_id → checksum (P1) | semantic/event dedup |
| NLP | pipeline + metrics tests | — |
| Editorial | review_queue, owner comment gate | формализованный fact/opinion contract в схеме |
| Media | contract, upload, statuses | полный prod E2E Blog+Telegram+archive |
| Telemetry | `last_collect_report.json` (агрегат) | per-source: last success/fail, latency, parsed, dupes, rejected |
| Health | `check_bot_health.py` (process/DB) | Content Engine health |
| Alerts | collect failure → Telegram (частично) | пороги по источнику, SLA |
| KB | — | Knowledge Agent, экспорт в MyWave KB |
| Contacts | `ContactsParser` | consent/permission policy |
| Multi-channel | Telegram (+ Blog sync path) | channel adapters matrix |
| Prod vs dev | ветка `feat/blog-editorial-media-pipeline` | выравнивание с production release |

---

## 6. Definition of Done по этапам

### Этап 1 — Release и telemetry
- На каждый источник: `last_success`, `last_failure`, `latency_ms`, `collected`, `parsed`, `duplicates`, `rejected`, `errors`.
- Отчёт переживает рестарт (SQLite или sheet `source_health`).
- Alert при N подряд failures / stale success > SLA.
- Health-скрипт различает process-alive и content-pipeline-ok.

### Этап 2 — Controlled material E2E
Один материал:  
`source → raw → normalize → dedup → NLP → Owner comment → media → Telegram → Blog → archive`  
с одним сквозным ID; повторный collect без дубля; Owner GO на prod.

### Этап 3 — Semantic dedup
- Cluster по событию (title/summary embedding или rule+similarity).
- Один `event_id`; публикации из cluster — с provenance источников.

### Этап 4 — Editorial integrity
- Поля/контракт: fact vs summary vs owner vs interpretation.
- Авто-publish запрещён policy-тестом.

### Этап 5 — Media production
- MIME, safe URL, retry, provenance, thumbnails, source↔media links; statuses из Blog Editorial Contract.

### Этап 6 — Knowledge (только после E2E)
- Публикация = кандидат в знание, не авто-знание.
- Извлечение сущностей → контракт экспорта в MyWave KB.

### Этап 7 — Contacts / audience
- consent, source, interest, region, interaction history; запрет агрессивной рассылки.

### Этап 8 — Multi-channel
- Site / social = adapters; бизнес-логика в одном Publishing Agent.

### 100% Content Engine
Полный путь + measure + semantic dedup + editorial + media + archive + knowledge + analytics.

---

## 7. Anti-patterns (запрещено)

- Красивые AI-фичи до доказанного E2E одного материала.
- Считать `systemd active` доказательством Content Engine.
- Дублировать KB внутри ParserNews как вторую «истину».
- Auto-publish в обход editorial.
- Копировать бизнес-логику в каждый канал вместо adapter.
- Массовый writeback Sheets / prod deploy без Owner GO.

---

## 8. Следующий технический шаг

**Этап 1 — реализован в коде (ветка разработки):**
- таблица `source_health` (миграция `003_source_health.sql`);
- запись метрик из `parse_all_sources` и manual collect;
- расширенный `last_collect_report.json`;
- `check_bot_health.py` различает process-ok и content_pipeline;
- alerts при fail-streak / degraded pipeline;
- `/stats` и `/report` показывают Source health;
- scaffolds: semantic dedup (flag off), editorial layers, contact consent, KB, channel adapters;
- `scripts/content_e2e_trace.py` + `scripts/content_engine_gate.py`;
- media: SSRF-check upload/response URL, provenance metadata.

**Следующий:** Этап 2 Controlled E2E на staging/prod только с Owner GO.
