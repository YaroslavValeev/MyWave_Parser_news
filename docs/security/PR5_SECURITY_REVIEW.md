# Security review: PR #5 (draft)

**PR:** https://github.com/YaroslavValeev/MyWave_Parser_news/pull/5  
**Branch:** `cursor/predeploy-checks-11a1`  
**Вердикт:** не merge as-is. Cherry-pick полезного; остальное закрыть/rebase в `feat/blog-media-editorial-pipeline`.

## Взять

- Удаление tracked `.env` из индекса
- Синтаксические/import fixes для bot entrypoints
- Offline-safe website collector + тесты
- Идея `SiteMediaClient` / media upload token config

## Не брать слепо

- Bump Python 3.12 без согласования с prod **3.11**
- Любые коммиты, которые снова могут затянуть секреты
- Merge только потому что tests green

## Чеклист media upload (реализовано в pipeline-ветке)

- [x] Retries (max 3, exponential backoff)
- [x] Timeouts из config
- [x] Idempotency key (`X-Idempotency-Key`)
- [x] MIME allowlist + magic bytes
- [x] File size limits
- [x] Public URL validation через `normalize_raw_feed_media_ref`
- [x] SSRF guard для исходящих fetch (`utils/safe_http.py`)
- [ ] Allowed hosts для upload endpoint — endpoint из config (не user input)

## Owner GO

Production не менять, пока rotation checklist в `SECRET_ROTATION_RUNBOOK.md` не подписан.
