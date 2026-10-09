# Blog Media Backfill Runbook (docs-only)

**Статус:** `DRY-RUN READY — NO MASS WRITEBACK`  
**Владелец:** Parser News / TGbotAdmin (+ Site для API boundary)  
**Дата:** 2026-07-13  
**GM gate:** backfill **запрещён** до отдельного approval (GM + Owner)

Dry-run script:

```bash
python scripts/blog_media_backfill_dry_run.py --source json --out docs/integration/artifacts/
# или (read-only Sheets):
python scripts/blog_media_backfill_dry_run.py --source sheets --out docs/integration/artifacts/
```

Отчёт: `docs/integration/artifacts/BACKFILL_DRY_RUN_<date>.md` (`proposed_writes=0`).


---

## 0. Guardrails (обязательно)

| Действие | Сейчас |
|----------|--------|
| Подготовка runbook / классификация | ✅ разрешено |
| Read-only audit / поиск файлов на диске | ✅ разрешено |
| Sheet edit / mass writeback | ❌ **запрещено** |
| Parser mass upload | ❌ **запрещено** |
| Production deploy / main merge | ❌ **запрещено** |
| `mywave-site` restart | ❌ **запрещено** |
| Staging backfill execution | ❌ до GM approval |

**Parser prod config (PASS, 2026-06-16):**

```text
SITE_BASE_URL=https://mywavewake.ru
MEDIA_UPLOAD_ENDPOINT=/api/blog/media/upload
WHISPER_MODEL=whisper-1
UPLOAD URL=https://mywavewake.ru/api/blog/media/upload
MEDIA_UPLOAD_TOKEN_PRESENT=yes
parser-news-bot=active
```

---

## 1. Контекст инцидента

Site уже **не рендерит** `127.0.0.1` (код Site PASS).  
Остаётся **физическое отсутствие файлов** и **битые ссылки** в `raw_feed`.

### 1.1. Сводка Owner/audit (2026-06-16)

```text
total_media_rows=30
placeholders=10          # Place1Logo / fallback markers
review_media_total=13
review_media_http_200_staging=0
review_media_http_200_prod=0
review_media_missing_both=13
external_images=7
```

**Вывод GM:**

- **13** `review_media` URL → **404** на staging **и** prod → класс **C** (missing file), если Parser не найдёт оригинал.
- **10** Place1Logo → отдельная классификация A/B/C/D.
- **7** external CDN → класс **D** (обычно OK, не трогать без причины).

---

## 2. Input artifacts

### 2.1. Источник на prod VPS (`62.113.42.227`)

| Файл | Назначение |
|------|------------|
| `/tmp/blog-media-audit.csv` | полный audit export |
| `/tmp/blog-media-final-audit-20260616.txt` | human-readable summary |
| `/tmp/blog-media-backfill-candidates-20260616.json` | кандидаты на backfill |
| `/tmp/gm-blog-media-backfill-request-20260616.txt` | GM request snapshot |

### 2.2. Копирование в репозиторий (read-only, без Sheet)

**С Windows (после SSH-доступа):**

```powershell
scp root@62.113.42.227:/tmp/blog-media-audit.csv "f:\Проекты MyWave\Ярик\MyWave_Parser_news\docs\integration\artifacts\blog-media-audit.csv"

scp root@62.113.42.227:/tmp/blog-media-final-audit-20260616.txt "f:\Проекты MyWave\Ярик\MyWave_Parser_news\docs\integration\artifacts\blog-media-final-audit-20260616.txt"

scp root@62.113.42.227:/tmp/blog-media-backfill-candidates-20260616.json "f:\Проекты MyWave\Ярик\MyWave_Parser_news\docs\integration\artifacts\blog-media-backfill-candidates-20260616.json"

scp root@62.113.42.227:/tmp/gm-blog-media-backfill-request-20260616.txt "f:\Проекты MyWave\Ярик\MyWave_Parser_news\docs\integration\artifacts\gm-blog-media-backfill-request-20260616.txt"
```

**На сервере Parser** (создать каталог артефактов):

```bash
mkdir -p /opt/bot3/parser-new-bot/docs/integration/artifacts
cp /tmp/blog-media-* /opt/bot3/parser-new-bot/docs/integration/artifacts/ 2>/dev/null || true
```

### 2.3. Что перенести в таблицы runbook

Из JSON/CSV заполнить **Appendix A** (ниже):

- 13 broken `review_media` rows: `row_number`, `id`, `slug`, `title`, `old URL`, staging/prod HTTP status
- 10 Place1Logo rows: те же поля + `cover_image_url` / `images` / `media_json`

---

## 3. Классификация строк (A / B / C / D)

| Класс | Описание | Действие backfill |
|-------|----------|-------------------|
| **A** | В источнике **нет** медиа (текст-only, нет t.me/media) | Place1Logo / ручная обложка Owner **или** оставить fallback |
| **B** | В Sheet **битый URL** (localhost, относительный `/static/` без файла на Site) | writeback после reupload **или** исправление URL |
| **C** | Файл **отсутствует на диске** Site (staging+prod 404), но может быть у Parser | reupload через `POST /api/blog/media/upload` → writeback `cover_image_url` |
| **D** | **Внешний CDN** HTTP 200 | **не трогать** |

### 3.2. Итог source search (GM, 2026-06-18) — **FINAL**

**13 broken `review_media` строк:**

| Поле | Значение |
|------|----------|
| Класс | **C** (valid URL path, file missing on Site disk) + **source missing** на Parser |
| `Original media found` | **0/13** (`review_*` basenames) |
| Automatic Parser reupload | **not possible** |
| `Rows proposed for automatic reupload` | **0** |
| Proposed action | **manual Owner cover / editor cover / keep placeholder** — решение Owner по каждой строке |
| Parser execution | **not started** |

Локальный cache Parser (`item-*-owner-cover.jpg`, 10 файлов) **не соответствует** 13 `review_*` именам из Sheet — не использовать для auto-reupload.

### 3.3. Place1Logo: расхождение 10 vs 11

| Источник | Count | Дата среза |
|----------|-------|------------|
| Owner audit `blog-media-audit.csv` (`kind=place1logo`) | **10** | 2026-06-16 |
| Prod API `/api/blog/posts` (рендер fallback) | **11** | 2026-06-18 |

**Дополнительная строка (в API, не в audit 2026-06-16):**

| Поле | Значение |
|------|----------|
| `raw_feed` row_number | **88** |
| id | **112** |
| slug | `альфа-банк-серф-кап-2026-открыта-регистрация-на-карнавальный-заезд-и-заезды-для-любителей-7f6ffa` |
| title | 🏄 АЛЬФА-БАНК СЕРФ КАП 2026 — ОТКРЫТА РЕГИСТРАЦИЯ… |
| class | **A** (пустой `cover_image_url` в Sheet → Site fallback Place1Logo) |
| reason | Опубликована **после** Owner audit; в `blog-media-audit.csv` отсутствует |

**Канон для GM backfill scope:** `acceptable Place1Logo = **10**` (по audit CSV).  
Строка id **112** — post-audit; Owner решает отдельно (keep fallback / manual cover).

---

## 4. Source media search (Parser News, read-only)

**Не удалять файлы. Не загружать без approval.**

### 4.1. Локальные каталоги Parser (prod)

```bash
cd /opt/bot3/parser-new-bot

# review media cache (Telegram album / cover download)
find downloads/review_media -type f 2>/dev/null | head -50
ls -la downloads/review_media 2>/dev/null | wc -l

# прочие media
find media downloads -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) 2>/dev/null | wc -l

# SQLite: review queue + media paths
sqlite3 data.db "SELECT id, title, substr(content,1,80) FROM items ORDER BY id DESC LIMIT 30;"
sqlite3 data.db "SELECT item_id, substr(extra,1,200) FROM nlp_results WHERE extra LIKE '%review_media%' OR extra LIKE '%cover%' LIMIT 20;"
```

### 4.2. Поля `raw_feed` (Google Sheet, read-only через API)

Колонки (канон `utils/sheet_schema.py`):

- `cover_image_url` — **primary** для writeback
- `image_url`, `images`, `raw_media`, `media_json`, `cover_image_path`

**Read-only диагностика (уже в репо):**

```bash
cd /opt/bot3/parser-new-bot
source venv/bin/activate

# контракт raw_feed media (не пишет в Sheet)
python scripts/diagnose_raw_feed_media.py --limit 30

# поток Parser → site API → raw_feed (read-only)
python scripts/check_media_flow.py --base-url https://mywavewake.ru
```

### 4.3. Логи upload / public_url

```bash
journalctl -u parser-news-bot --since "2026-06-01" | grep -iE 'media_upload|cover|public_url|owner_cover' | tail -80
grep -iE 'media_upload|owner_cover_auto' /opt/bot3/parser-new-bot/logs/mywave-bot.log 2>/dev/null | tail -50
```

### 4.4. Сопоставление audit row → локальный файл

Для каждой строки из `blog-media-backfill-candidates-20260616.json`:

1. Извлечь `slug` / `item_id` / basename из URL (`photo_2026-06-13_19-30-56.jpg`).
2. Поиск:

```bash
BASENAME="photo_2026-06-13_19-30-56.jpg"   # подставить из audit
find /opt/bot3/parser-new-bot/downloads/review_media /opt/bot3/parser-new-bot/media -name "$BASENAME" 2>/dev/null
```

3. Если файл найден → кандидат **reupload (класс C→fix)**.  
4. Если нет → проверить `source_url` / Telegram re-fetch (только в плане, не выполнять).

---

## 5. Reupload / writeback plan (per row template)

**Выполнение — только после §9 Production gate.**

| Поле | Значение |
|------|----------|
| `row_number` | из raw_feed |
| `id` / `slug` / `title` | из audit |
| `class` | A/B/C/D |
| `source` | `local:/path` **или** `url:https://...` **или** `none` |
| `upload_target` | `POST https://mywavewake.ru/api/blog/media/upload` |
| `auth` | `Authorization: Bearer <MEDIA_UPLOAD_TOKEN>` + `X-Media-Upload-Token` |
| `expected` | HTTP **201** + `public_url` / `url` в JSON |
| **Sheet primary** | `cover_image_url` |
| **Sheet optional** | `image_url`, первая строка `images`, `media_json.path` |
| `cache_invalidate` | §7 |
| `verify` | HEAD/GET `public_url` → 200 image/*; `/blog` без broken URL |
| `rollback` | restore `cover_image_url` из snapshot §6 |

### 5.1. Parser code path (reference, not mass-run now)

- Upload: `services/media_upload.py` → `upload_cover_image()` / `prepare_item_media_for_raw_feed()`
- Writeback Sheet: `services/raw_feed_sync.py` → `sync_media_fields()` (batch, идемпотентно)
- Auto после комментария Owner: `maybe_autoupload_local_cover_and_sync_sheet()` в `services/media_upload.py`

### 5.2. Dry-run upload (одна строка, **только после GM approval**)

```bash
# ПРИМЕР — НЕ ЗАПУСКАТЬ ДО APPROVAL
cd /opt/bot3/parser-new-bot && source venv/bin/activate
python3 -c "
from pathlib import Path
from services.media_upload import upload_cover_image
r = upload_cover_image(Path('downloads/review_media/FILE.jpg'), item_id=ITEM_ID, item={})
print(r)
"
```

---

## 6. Snapshot / rollback (обязательно перед writeback)

### 6.1. Export raw_feed

```bash
# READ-ONLY export через Sheets UI или gspread script (без update)
# Минимум колонки:
# row_number, id, slug, raw_title, cover_image_url, image_url, images, raw_media, media_json, status
```

Сохранить как:

```text
docs/integration/artifacts/raw_feed_snapshot_YYYYMMDD_HHMM.csv
```

### 6.2. Change table (ведётся вручную / CSV)

| row_number | slug | old_cover | new_cover | actor | timestamp | rollback_status |
|------------|------|-----------|-----------|-------|-----------|-----------------|
| … | … | … | … | parser-bot | ISO8601 | pending/done |

### 6.3. Rollback

1. Восстановить колонки из `raw_feed_snapshot_*.csv` (batch update по `row_number`).
2. `POST /api/blog/cache/invalidate` (§7).
3. Проверить `/blog` smoke (§8).

---

## 7. Cache invalidate (blog only)

**Не использовать** `POST /api/competitions/cache/invalidate` для blog media.

### 7.1. Канон Parser (.env)

```env
SITE_BASE_URL=https://mywavewake.ru
SITE_CACHE_INVALIDATE_ENDPOINT=/api/blog/cache/invalidate
SITE_CACHE_INVALIDATE_TOKEN=          # пусто → fallback MEDIA_UPLOAD_TOKEN
```

Код: `services/site_cache.py` → `invalidate_site_blog_cache()`.

### 7.2. Smoke (read-only, без mass invalidate)

```bash
cd /opt/bot3/parser-new-bot
source venv/bin/activate
python3 <<'PY'
from services.site_cache import cache_invalidate_url, cache_invalidate_configured
print("configured:", cache_invalidate_configured())
print("url:", cache_invalidate_url())
PY
```

Ожидаемо: `https://mywavewake.ru/api/blog/cache/invalidate`

### 7.3. POST после backfill (**после approval**)

```bash
TOKEN="$(grep -E '^MEDIA_UPLOAD_TOKEN=' /opt/bot3/parser-new-bot/.env | cut -d= -f2- | tr -d '\"')"
curl -sS -X POST "https://mywavewake.ru/api/blog/cache/invalidate" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"reason":"blog_media_backfill_batch_1"}' \
  -w "\nHTTP %{http_code}\n"
```

Ожидаемо: HTTP 200/201/202/204.

**Site:** подтвердить, что route совпадает (если другой — обновить `SITE_CACHE_INVALIDATE_ENDPOINT`).

---

## 8. Smoke checklist (staging, после backfill batch)

- [ ] `/blog` — нет `127.0.0.1` / localhost в HTML/API
- [ ] `broken review_media` count **уменьшился** (повтор audit)
- [ ] обновлённые `cover_image_url` → HTTP **200** + `image/*`
- [ ] Place1Logo count уменьшился **только** на ожидаемых slug
- [ ] external images (класс D) по-прежнему 200
- [ ] `journalctl -u parser-news-bot` — нет ошибок upload/cache
- [ ] Site logs — нет 5xx на `/api/blog/media/upload`

---

## 9. Production gate

Backfill на **production** только если:

- [ ] GM approval
- [ ] Owner approval
- [ ] `raw_feed` snapshot (§6)
- [ ] dry-run report (1–3 строки на staging)
- [ ] staging PASS (§8)
- [ ] rollback plan reviewed
- [ ] change table готова

**Порядок:** staging batch → smoke → GM sign-off → prod batch ≤ N rows → invalidate → smoke.

---

## 10. Роли

| Задача | Owner |
|--------|-------|
| Классификация A–D, поиск локальных файлов | Parser News |
| Подтверждение upload/cache API | Site |
| Ручная обложка (класс A) | Owner / Editor |
| GM approval | GM |
| Sheet snapshot / rollback execute | Parser News (+ Site при необходимости) |
| Staging smoke | Parser News + Site |

---

## Appendix A — Row inventory (filled from prod API + Owner audit, 2026-06-16)

Источник: `GET https://mywavewake.ru/api/blog/posts?limit=50` + audit metrics.  
Артефакт: `docs/integration/artifacts/blog-media-inventory-from-api-20260616.json`  
`row_number` из Sheet — после копирования `/tmp/blog-media-audit.csv` на сервер (см. команды ниже).

### A.1. Missing `review_media` — 13 rows (class **C / source missing**)

**Automatic reupload: 0.** Proposed action for all 13: **manual Owner/editor cover** (or keep placeholder by Owner decision).

| idx | slug | title | old `cover_image_url` | staging | prod | Parser source | class | proposed action |
|-----|------|-------|----------------------|---------|------|---------------|-------|-----------------|
| 12 | `уважаемые-коллеги-0e3a37` | Уважаемые коллеги! | `http://127.0.0.1:5000/static/uploads/review_media/review_20260505_170115_adabf7e1023a.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 13 | `вебинар-профессиональное-образование-в-серфинге-d82118` | ВЕБИНАР… | `…/review_20260505_170110_a9336ef014a1.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 14 | `делимся-актуальным-списком-каналов-…-f0dd4a` | Делимся актуальным списком… | `…/review_20260506_091734_291cadc78ba3.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 15 | `календарь-спортивных-мероприятий-…-9701a1` | Календарь спортивных мероприятий… | `…/review_20260505_170102_1f39cd4cd258.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 16 | `пост-из-провейксерф-проект-о-вейксерфинге-c08268` | Пост из Провейксерф… | `…/review_20260505_170109_3b9417a1465b.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 18 | `тренировочные-сборы-в-юар-…-8d9a0a` | Тренировочные сборы в ЮАР… | `…/review_20260425_180507_8f47bceb9d81.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 21 | `22-апреля-в-москве-состоялся-форум-sport-b2b-…-70feb6` | 22 апреля… Sport B2B… | `…/review_20260505_170114_7278a093d7c4.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 24 | `уважаемые-коллеги-d1ee59` | Уважаемые коллеги! | `…/review_20260423_122915_b6d68db194f0.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 25 | `готовимся-проверяем-малышек-после-зимы-fb508e` | Готовимся проверяем малышек… | `…/review_20260505_170121_c193cc37be59.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 26 | `международная-федерация-воднолыжного-спорта-…-3e313b` | Международная федерация… | `…/review_20260505_170122_a1a68b2395bb.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 27 | `пост-из-wakedivision-c73dfe` | Пост из WakeDivision | `…/review_20260505_170123_f717d2a27bd5.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 28 | `пост-из-wakedivision-0e5566` | Пост из WakeDivision | `…/review_20260505_170126_6b9ce8ca1616.jpg` | 404 | 404 | missing | C | manual Owner cover |
| 29 | `пост-из-wakediary-82965d` | Пост из Wakediary | `…/review_20260505_170128_ce5fccebdb07.jpg` | 404 | 404 | missing | C | manual Owner cover |

### A.2. Place1Logo — **10** rows (Owner audit CSV 2026-06-16, class **A**)

| slug (short) | title | class | proposed action | Owner decision |
|--------------|-------|-------|-----------------|----------------|
| `21-июня-…-2a38a4` | 12-й Международный день йоги… | A | keep Place1Logo / manual | yes |
| `18-19-июня-…-093f65` | Кубок Москвы Wakedivision… | A | keep / manual | yes |
| `весна-и-лето-…-72b32a` | Весна и лето… | A | keep / manual | yes |
| `потихоньку-…-d64592` | Потихоньку раскачиваем… | A | keep / manual | yes |
| `дорогие-любименькие-…-1c383c` | Новый сезон! Ура!! | A | keep / manual | yes |
| `с-днем-победы-309928` | С Днём Победы! | A | keep / manual | yes |
| `wsws-centurion-…-46771d` | WSWS Japan Open 2025 | A | keep / manual | yes |
| `ждемс-7f53f8` | Ждемс👀 | A | keep / manual | yes |
| `начинаем-собирать-…-e56b06` | Календарь соревнований 2026 | A | keep / manual | yes |
| `федерация-водных-лыж-…-6c1da8` | Расписание соревнований | A | keep / manual | yes |

**Post-audit (+1, не в audit 2026-06-16):** `row_number=88`, `id=112`, slug `альфа-банк-серф-кап-2026-…-7f6ffa`, title «АЛЬФА-БАНК СЕРФ КАП 2026…», class **A** — Owner decision отдельно.

### A.3. External CDN — 7 rows (class **D**, no action)

| idx | slug | url | HTTP | class | action |
|-----|------|-----|------|-------|--------|
| 3 | `pleasurecraft-marine-lake-murray-clean-up-68d30a` | wakeboardingmag.com …cleanup.jpg | 200 | D | no action |
| 8 | `mastercraft-benefits-st-jude-…-072b03` | wakeboardingmag.com …STSL…jpg | 404* | D | no action (CDN; не review_media) |
| 10 | `voltaic-marine-enters-the-game-with-the-aew24-7a6741` | unleashedwakemag.com …Voltaic… | 200 | D | no action |
| 19 | `watersports-industry-association-wsia-…-4fa7c6` | wakeboardingmag.com …WSIA… | 200 | D | no action |
| 20 | `mastercraft-s-let-her-rip-campaign-returns-in-2026-363763` | wakeboardingmag.com …Let-Her-rip… | 200 | D | no action |
| 30 | `brisbane-2032-le-wake-cable-…-ff49cc` | unleashedwakemag.com logo | 200 | D | no action |
| 31 | `2026-mastercraft-wwa-rider-experience-schedule-01e956` | thewwa.com header | 200 | D | no action |

\*HEAD 404 на один CDN URL — вне scope backfill `review_media`; не менять без Owner.

---

## Appendix B — GM final response (Parser News, 2026-06-18)

```text
Runbook PR / commit:
  docs/integration/BLOG_MEDIA_BACKFILL_RUNBOOK.md
  Branch: docs/blog-media-backfill-runbook-parser
  Commit: 91d5e1d

Artifacts on VPS: yes
SOURCE_SCAN file: docs/integration/artifacts/SOURCE_SCAN_20260618.txt

Rows classified:
  review_media C/source missing: 13
  manual Owner cover: 13
  acceptable Place1Logo: 10
  external D: 7

Count discrepancy explanation:
  "11 Place1Logo" was prod API count on 2026-06-18 (rendered fallbacks).
  Owner audit CSV 2026-06-16 has placeholders=10.
  Extra row (post-audit, not in audit CSV):
    row_number=88, id=112,
    slug=альфа-банк-серф-кап-2026-открыта-регистрация-на-карнавальный-заезд-и-заезды-для-любителей-7f6ffa,
    title=АЛЬФА-БАНК СЕРФ КАП 2026…, class=A.

13 review_media rows = class C with source missing.
Proposed action = manual Owner cover / editor cover / keep placeholder by Owner decision.
Rows proposed for automatic reupload = 0.

Original media search locations checked:
  downloads/review_media, media/, downloads/, journalctl, diagnose_raw_feed_media, check_media_flow

Original media found: 0/13
Rows proposed for reupload: 0

Parser prod smoke: upload empty 400 OK, cache invalidate 200 OK
Sheet changed: no
Execution not started: yes
Production deploy: no

Need from Owner: manual cover decisions for 13 review_media rows; optional decision on id=112
Need from Site: fix HTTP 500 on POST /api/blog/media/upload (diag item-112)
```

---

## Appendix C — Связанные документы

- `docs/SERVER_DEPLOY_CANON_RU.md` — prod paths
- `scripts/diagnose_raw_feed_media.py` — read-only Sheet diagnostic
- `scripts/check_media_flow.py` — read-only flow check
- `services/media_upload.py` — upload contract
- `services/site_cache.py` — blog cache invalidate
- `.env.example` — `SITE_CACHE_INVALIDATE_ENDPOINT=/api/blog/cache/invalidate`

---

**Версия:** 1.3  
**Статус (GM 2026-06-18):**

```text
Parser source scan: CLOSED
Site upload blocker: CLOSED (PR45 hotfix)
Parser post-PR45 smoke: PASS (201, public_url prod, no localhost)
Backfill execution: BLOCKED (no GM/Owner approval)
Sheet changed: no
```

**Future upload** (новые публикации, owner cover after comment): **READY**.  
**13-row backfill / mass upload / Sheet writeback:** **запрещено** без отдельного approval.

---

## 12. Команды для сервера (Parser VPS `62.113.42.227`)

См. также одностраничник: выполнить блоки **1→6** по порядку на сервере (копировать в SSH).
