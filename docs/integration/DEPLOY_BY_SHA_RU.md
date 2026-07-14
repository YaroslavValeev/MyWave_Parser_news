# Prod deploy by exact release SHA — ParserNews

**Проект:** ParserNews  
**Сервер:** `62.113.42.227`  
**Терминал:** SSH `root@62.113.42.227`  
**Рабочая директория:** `/opt/bot3/parser-new-bot`  
**Service:** `parser-news-bot.service`  
**Запрещено трогать:** Site Admin, YClients, MyWaveTour, `mywave-site` restart без отдельного GO  

**Правило:** `git pull` / `git pull origin main` **не принимать**. Только `git fetch` + `git checkout <RELEASE_SHA>`.

---

## Release pins (обновлять при каждом candidate release)

| Role | SHA | Notes |
|------|-----|-------|
| **RELEASE_SHA** (PR #6 head / candidate) | `3a45f51d7cac16ac9884c9f312d8af0b95341153` | HOLD until rotation complete |
| **ROLLBACK_SHA** (current `origin/main`) | `a2c5d439212cf22d22771435fabe334017999002` | Last known main before PR #6 |

После каждого нового push в PR обновляйте RELEASE_SHA в этом файле и в PR комментарии.

---

## Gate before deploy

- [ ] [`OWNER_ROTATION_CHECKLIST.md`](../security/OWNER_ROTATION_CHECKLIST.md) — no `pending Owner` for used credentials
- [ ] CI green on RELEASE_SHA (pytest + secret-scan-tree)
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
git rev-parse HEAD
git cat-file -t 3a45f51d7cac16ac9884c9f312d8af0b95341153
git ls-files .env credentials.json
# Expected: empty output from ls-files
test -f .env && echo env_present || echo env_MISSING
test -f credentials.json && echo gcp_present || echo gcp_MISSING
# Presence only — never cat/print secrets
python3 - <<'PY'
import os
from pathlib import Path
# Load .env keys names only if python-dotenv available; else skip
keys = [
  "TELEGRAM_BOT_TOKEN","OPENAI_API_KEY","YOUTUBE_API_KEY",
  "MEDIA_UPLOAD_TOKEN","TELEGRAM_API_HASH_USER","PROXY_PASS",
]
# Do not print values
for k in keys:
    print(f"{k}={'set' if os.environ.get(k) else 'unset_in_process'}")
print("use systemctl EnvironmentFile or manual check that prod .env was rotated")
PY
```

---

## Exact deploy (Owner GO required)

```bash
export RELEASE_SHA=3a45f51d7cac16ac9884c9f312d8af0b95341153
export ROLLBACK_SHA=a2c5d439212cf22d22771435fabe334017999002

cd /opt/bot3/parser-new-bot
sudo systemctl stop parser-news-bot
git fetch origin
git checkout --force "$RELEASE_SHA"
git rev-parse HEAD
# Expected: 3a45f51d7cac16ac9884c9f312d8af0b95341153

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

**Expected:** `active (running)`, health exit 0, `HEAD` == RELEASE_SHA.

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

Controlled post details: [`CONTROLLED_POST_E2E.md`](CONTROLLED_POST_E2E.md).

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
