# Secret Rotation Runbook (ParserNews)

**Статус gate:** MERGE / prod deploy **HOLD** until Owner rotation complete.  
**Правило:** не вставлять значения секретов в чат, PR, логи или этот документ.

Канонический Owner checklist + status matrix:

→ [`OWNER_ROTATION_CHECKLIST.md`](OWNER_ROTATION_CHECKLIST.md)

History rewrite (только Owner GO):

→ [`HISTORY_PURGE_PLAN.md`](HISTORY_PURGE_PLAN.md)

Deploy by exact SHA (не `git pull`):

→ [`../integration/DEPLOY_BY_SHA_RU.md`](../integration/DEPLOY_BY_SHA_RU.md)

## Findings (без значений)

| Артефакт | Severity | Действие |
|----------|----------|----------|
| `.env` был в git index | P0 | untrack (done in PR #6) + **rotation** |
| `credentials.json` (GCP SA) был в git | P0 | untrack + revoke key in GCP IAM |
| История публичного репо | P0 | [`HISTORY_PURGE_PLAN.md`](HISTORY_PURGE_PLAN.md) — Owner GO |
| Secret scan | P1 | CI: required **current-tree** + report **history** |

## Local verify (no values)

```powershell
git ls-files .env credentials.json
# Expected: empty
```

## After rotation + Owner GO deploy

Follow [`DEPLOY_BY_SHA_RU.md`](../integration/DEPLOY_BY_SHA_RU.md) with pinned `RELEASE_SHA`.
