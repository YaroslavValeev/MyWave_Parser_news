# Controlled post E2E (Owner GO required)

**Проект:** ParserNews  
**Production не менять** без явного Owner GO после security rotation.

## Preconditions

- [ ] `docs/security/SECRET_ROTATION_RUNBOOK.md` checklist выполнен
- [ ] `.env` / `credentials.json` не в git (`git ls-files` пусто)
- [ ] CI green (pytest + secret-scan)
- [ ] Dry-run report: `docs/integration/artifacts/BACKFILL_DRY_RUN_*.md`

## One controlled post

**Сервер:** `62.113.42.227`  
**Директория:** `/opt/bot3/parser-new-bot`  
**Service:** `parser-news-bot.service`  
**Запрещено:** Site Admin, YClients, MyWaveTour, `mywave-site` restart без отдельного GO

```bash
cd /opt/bot3/parser-new-bot
source venv/bin/activate
python scripts/check_bot_health.py
# В Telegram Admin: открыть один материал → Retry media → Approve → проверить raw_feed
```

Expected:

- `media_status` ∈ {image_ready, video_ready, external_video, missing}
- `video_url` / `embed_url` не только внутри `content_md`
- Site Blog card показывает cover/video

## Rollback

```bash
cd /opt/bot3/parser-new-bot
git checkout <previous_sha>
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart parser-news-bot
```

Sheets: ручной откат строки по snapshot (не автоматический).
