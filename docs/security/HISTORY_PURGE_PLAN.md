# Git History Cleanup Plan — ParserNews (NO EXECUTION without Owner GO)

**Статус:** PROPOSAL ONLY  
**Force-push / history rewrite:** **запрещены** без отдельного Owner GO.  
**Цель:** удалить из истории `.env`, `credentials.json`, sessions, tracked venv и крупные секретные артефакты.

## Почему нужно

Файлы сняты с индекса в PR #6, но **публичная история** всё ещё может содержать:

| Path / pattern | Risk |
|----------------|------|
| `.env` | Bot/API tokens, proxy |
| `credentials.json` | GCP SA private_key |
| `*.session`, `session_string.txt` | Telethon auth |
| `.venv/`, `venv/` | bloat + accidental secret copies |
| `backups/credentials-*.json` | if ever committed |
| Real private keys (`*.pem`) | if ever committed |

## Preconditions (Owner)

1. Rotation checklist: все exposed credentials → `revoked` / `rotated` ([`OWNER_ROTATION_CHECKLIST.md`](OWNER_ROTATION_CHECKLIST.md)).
2. Backup зеркала: `git clone --mirror` в закрытое хранилище Owner.
3. Согласовать downtime: все локальные/CI клоны после rewrite должны re-clone.
4. Явный Owner GO текст: `GO history-purge MyWave_Parser_news <date>`.

## Recommended tool

`git filter-repo` (предпочтительно) или BFG.

## Exact commands (выполнять только после Owner GO)

На изолированной машине Owner:

```bash
# 0) Mirror backup
git clone --mirror https://github.com/YaroslavValeev/MyWave_Parser_news.git MyWave_Parser_news.git.backup
cd MyWave_Parser_news.git.backup

# 1) Working copy from mirror for filter-repo
git clone MyWave_Parser_news.git.backup MyWave_Parser_news-purge
cd MyWave_Parser_news-purge

# 2) Remove secret paths from ALL history
git filter-repo --force \
  --path .env --invert-paths

git filter-repo --force \
  --path credentials.json --invert-paths

git filter-repo --force \
  --path-glob '*.session' --invert-paths

git filter-repo --force \
  --path session_string.txt --invert-paths

git filter-repo --force \
  --path-glob 'backups/credentials-*.json' --invert-paths

# 3) Drop tracked venv trees from history (large)
git filter-repo --force \
  --path-glob '.venv/**' --invert-paths

git filter-repo --force \
  --path-glob 'venv/**' --invert-paths

# 4) Verify — expect NO hits on secret filenames in remaining blobs
git log --all --full-history -- .env credentials.json | head
git rev-list --objects --all | grep -E '(^|/)(\.env|credentials\.json)$' || echo "paths_absent_ok"

# 5) Push ONLY after explicit Owner GO for force-push
# git remote add origin https://github.com/YaroslavValeev/MyWave_Parser_news.git
# git push --force --all
# git push --force --tags
```

## After rewrite

- Все разработчики: удалить старый clone, `git clone` заново.
- CI caches: invalidate.
- Re-run **history** gitleaks job (должен стать green).
- Update protected branches / PR bases as needed.

## Rollback of purge

Только из `--mirror` backup (`MyWave_Parser_news.git.backup`). Без backup — необратимо.

## What NOT to do now

- Не запускать filter-repo на общем workstation без GO.
- Не force-push из CI или Agent.
- Не считать untrack в одном коммите заменой history purge.
