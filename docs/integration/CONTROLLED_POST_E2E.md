# Controlled post E2E (Owner GO required)

**Проект:** ParserNews  
**Production не менять** без явного Owner GO после security rotation.

## Preconditions

- [ ] [`OWNER_ROTATION_CHECKLIST.md`](../security/OWNER_ROTATION_CHECKLIST.md) — нет `pending Owner` для используемых credentials
- [ ] Deploy только по exact SHA: [`DEPLOY_BY_SHA_RU.md`](DEPLOY_BY_SHA_RU.md)
- [ ] `.env` / `credentials.json` не в git (`git ls-files` пусто)
- [ ] CI green: `secret-scan-tree` + pytest (history scan may warn until purge)
- [ ] Dry-run report: `proposed_writes=0`

## One controlled post

**Сервер:** `62.113.42.227`  
**Директория:** `/opt/bot3/parser-new-bot`  
**Service:** `parser-news-bot.service`  
**Запрещено:** Site Admin, YClients, MyWaveTour, `mywave-site` restart без отдельного GO

После `git checkout $RELEASE_SHA` (см. DEPLOY_BY_SHA):

```bash
cd /opt/bot3/parser-new-bot
source venv/bin/activate
python scripts/check_bot_health.py
python scripts/blog_media_backfill_dry_run.py --source json --out /tmp/backfill-dry-run/
# Expected in report: proposed_writes: 0
# В Telegram Admin: один материал → Retry media → Approve → проверить raw_feed
```

Expected:

- `media_status` ∈ {image_ready, video_ready, external_video, missing}
- `video_url` / `embed_url` не только внутри `content_md`
- Site Blog card показывает cover/video
- **Mass Sheet writeback запрещён** до отдельного Owner GO

## Rollback

Exact commands / SHA: [`DEPLOY_BY_SHA_RU.md`](DEPLOY_BY_SHA_RU.md) → `ROLLBACK_SHA=a2c5d439212cf22d22771435fabe334017999002`.

Sheets: ручной откат строки по snapshot (не автоматический).
