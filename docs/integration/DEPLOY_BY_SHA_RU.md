# Prod deploy by exact release SHA — ParserNews

**Проект:** ParserNews  
**Сервер:** `62.113.42.227`  
**Терминал:** SSH `root@62.113.42.227`  
**Рабочая директория:** `/opt/bot3/parser-new-bot`  
**Service:** `parser-news-bot.service`  
**Запрещено трогать:** Site Admin, YClients, MyWaveTour, `mywave-site` restart без отдельного GO  

**Правило:** `git pull` / `git pull origin main` **не принимать**. Только `git fetch` + `git checkout <RELEASE_SHA>`.

---

## Release pins

| Role | Value | Notes |
|------|-------|-------|
| **RELEASE_SHA** | resolve after fetch (see deploy) | Must equal `git rev-parse HEAD` after checkout |
| **Evidence tip (PR-B hotfix)** | `e5dd8feab920a4bbb1f1a120062751f85d34919b` | `create_router(repo, bot) -> Router` |
| **ROLLBACK_SHA** (`origin/main`) | `a2c5d439212cf22d22771435fabe334017999002` | Fixed rollback target |
| **Emergency file backup** | `/opt/bot3/parser-new-bot.bak.20260714_1452` | Prefer over broken stub SHA if bot won't start |

После deploy:

```bash
git rev-parse HEAD | tee /tmp/parsernews-deployed-sha.txt
```

---

## Gate before deploy

- [ ] [`OWNER_ROTATION_CHECKLIST.md`](../security/OWNER_ROTATION_CHECKLIST.md) — no `pending Owner` for used credentials
- [ ] CI green on RELEASE_SHA (pytest + `secret-scan-tree`)
- [ ] Dry-run: `proposed_writes=0`
- [ ] Owner GO: `GO deploy ParserNews <RELEASE_SHA>`

---

## Exact server precheck

```bash
cd /opt/bot3/parser-new-bot
hostname -I
systemctl is-active parser-news-bot
git remote -v
git fetch origin
export RELEASE_SHA=$(git rev-parse origin/feat/blog-media-editorial-pipeline)
export ROLLBACK_SHA=a2c5d439212cf22d22771435fabe334017999002
echo "RELEASE_SHA=$RELEASE_SHA"
echo "ROLLBACK_SHA=$ROLLBACK_SHA"
git cat-file -t "$RELEASE_SHA"
git ls-files .env credentials.json
# Expected: empty output from ls-files
test -f .env && echo env_present || echo env_MISSING
test -f credentials.json && echo gcp_present || echo gcp_MISSING
# Never cat/print secret values
```

---

## Exact deploy (Owner GO required)

```bash
cd /opt/bot3/parser-new-bot
git fetch origin
export RELEASE_SHA=$(git rev-parse origin/feat/blog-media-editorial-pipeline)
export ROLLBACK_SHA=a2c5d439212cf22d22771435fabe334017999002

sudo systemctl stop parser-news-bot
git checkout --force "$RELEASE_SHA"
git rev-parse HEAD | tee /tmp/parsernews-deployed-sha.txt
# Expected: equals $RELEASE_SHA

source venv/bin/activate
pip install -r requirements.txt
pytest tests/test_editorial_contract.py tests/test_video_providers.py tests/test_safe_http.py \
  tests/test_media_contract.py tests/test_media_upload.py tests/test_card_preview_text.py \
  tests/test_raw_feed_publish_contract.py tests/test_owner_review_telegram.py -q --tb=no || {
  echo "tests_failed_rollback";
  git checkout --force "$ROLLBACK_SHA";
  sudo systemctl start parser-news-bot;
  exit 1;
}

sudo systemctl start parser-news-bot
sudo systemctl status parser-news-bot --no-pager
python scripts/check_bot_health.py
```

**Expected:** `active (running)`, health exit 0, `HEAD` == `$RELEASE_SHA`.

---

## Exact smoke

```bash
cd /opt/bot3/parser-new-bot
source venv/bin/activate
python scripts/check_bot_health.py
python scripts/blog_media_backfill_dry_run.py --source json --out /tmp/backfill-dry-run/
grep -E 'proposed_writes|rows=' /tmp/backfill-dry-run/BACKFILL_DRY_RUN_*.md
# Expected: proposed_writes: 0
```

Telegram Admin (manual): один материал → Retry media → Approve.  
Site: одна Blog-карточка с корректным cover/video.  
**Mass Sheet writes:** запрещены (`proposed_writes=0` до отдельного Owner GO).

Details: [`CONTROLLED_POST_E2E.md`](CONTROLLED_POST_E2E.md).

---

## Exact rollback

```bash
export ROLLBACK_SHA=a2c5d439212cf22d22771435fabe334017999002
cd /opt/bot3/parser-new-bot
sudo systemctl stop parser-news-bot
git fetch origin
git checkout --force "$ROLLBACK_SHA"
git rev-parse HEAD
# Expected: a2c5d439212cf22d22771435fabe334017999002
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl start parser-news-bot
sudo systemctl status parser-news-bot --no-pager
python scripts/check_bot_health.py
```

Sheets: ручной откат строки по snapshot (не автоматический).

---

## Emergency restore (no git / broken stub SHA)

Если `fatal: not a git repository` **или** crash
`ValueError: router should be instance of Router not 'coroutine'`
на SHA `c7d6a37` / `f1df38f` — **не** оставаться на stub: вернуть рабочий tree из бэкапа.

```bash
sudo systemctl stop parser-news-bot
cd /opt/bot3
mv parser-new-bot "parser-new-bot.broken.$(date +%Y%m%d_%H%M)"
cp -a parser-new-bot.bak.20260714_1452 parser-new-bot
# fallback, если bak недоступен:
# cp -a parser-new-bot.old parser-new-bot
sudo systemctl start parser-news-bot
sudo systemctl is-active parser-news-bot
journalctl -u parser-news-bot -n 25 --no-pager
```

Ожидаемо: `active`, `Start polling`, `@MyWaveParcer_bot`.

Повторный git-deploy только после Owner GO и SHA **`e5dd8feab920a4bbb1f1a120062751f85d34919b`**
(или более нового tip PR-B), с копированием `.env` / `credentials.json` / `data/` из bak.
