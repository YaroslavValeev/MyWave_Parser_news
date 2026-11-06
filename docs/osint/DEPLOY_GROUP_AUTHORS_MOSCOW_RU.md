# Deploy: group authors (Moscow clubs only)

**Сервер:** `root@62.113.42.227`  
**Каталог:** `/opt/bot3/parser-new-bot`  
**Unit:** `parser-news-bot`  
**RELEASE_SHA:** `736e1d4e0dac73fd63b09f041aa11002d874a17d`  
**Ветка tip:** `origin/feat/blog-editorial-media-pipeline`  
**Правило:** не `git pull` — только `fetch` + `checkout` точного SHA.

---

## 1) Precheck (вставить целиком)

```bash
cd /opt/bot3/parser-new-bot
hostname -I
systemctl is-active parser-news-bot
git remote -v
git fetch origin
export RELEASE_SHA=736e1d4e0dac73fd63b09f041aa11002d874a17d
export ROLLBACK_SHA=$(git rev-parse HEAD)
echo "RELEASE_SHA=$RELEASE_SHA"
echo "ROLLBACK_SHA=$ROLLBACK_SHA"
git cat-file -t "$RELEASE_SHA"
test -f .env && echo env_present || echo env_MISSING
test -f session_name.session -o -n "$(grep -E '^TELETHON_STRING_SESSION=' .env 2>/dev/null)" && echo telethon_session_likely || echo telethon_session_CHECK
```

Ожидаемо: `RELEASE_SHA` тип `commit`, `env_present`.

---

## 2) Deploy (вставить целиком)

```bash
cd /opt/bot3/parser-new-bot
git fetch origin
export RELEASE_SHA=736e1d4e0dac73fd63b09f041aa11002d874a17d
export ROLLBACK_SHA=$(git rev-parse HEAD)

sudo systemctl stop parser-news-bot
git checkout --force "$RELEASE_SHA"
test "$(git rev-parse HEAD)" = "$RELEASE_SHA" || { echo "SHA_MISMATCH"; exit 1; }
git rev-parse HEAD | tee /tmp/parsernews-deployed-sha.txt

source venv/bin/activate
pip install -r requirements.txt

# Миграция 005_audience_tables.sql применится при старте БД / первом обращении.
# Smoke тесты аудитории:
pytest tests/test_group_authors_contract.py tests/test_group_authors_collect.py \
  tests/test_channel_commenters_contract.py -q --tb=line || {
  echo "tests_failed_rollback";
  git checkout --force "$ROLLBACK_SHA";
  sudo systemctl start parser-news-bot;
  exit 1;
}

# Env: дефолты уже Moscow-only в коде; явная запись не обязательна.
# Если хотите зафиксировать в .env (без печати секретов):
grep -q '^GROUP_AUTHORS_COLLECT_ENABLED=' .env 2>/dev/null || \
  echo 'GROUP_AUTHORS_COLLECT_ENABLED=true' >> .env
# Не дублируйте GROUP_AUTHORS_IDS, если уже устраивает default из settings.py

sudo systemctl start parser-news-bot
sudo systemctl status parser-news-bot --no-pager
python scripts/check_bot_health.py || true
```

Ожидаемо: `active (running)`, `HEAD` = `736e1d4e0dac73fd63b09f041aa11002d874a17d`.

---

## 3) Smoke после старта

В Telegram (оператор бота):

```text
/stats
/collect_group_authors @wakedivision
```

Потом полный chunk (по cron ~05:00 или вручную):

```text
/collect_group_authors
```

В логах:

```bash
journalctl -u parser-news-bot -n 80 --no-pager | egrep -i 'group_authors|collect_group|error|traceback' || true
```

---

## 4) Rollback

```bash
cd /opt/bot3/parser-new-bot
# подставьте ROLLBACK_SHA из precheck, если сохранили
export ROLLBACK_SHA=${ROLLBACK_SHA:-}
# или предыдущий известный SHA до деплоя
sudo systemctl stop parser-news-bot
git fetch origin
git checkout --force "$ROLLBACK_SHA"
sudo systemctl start parser-news-bot
sudo systemctl is-active parser-news-bot
git rev-parse HEAD
```
