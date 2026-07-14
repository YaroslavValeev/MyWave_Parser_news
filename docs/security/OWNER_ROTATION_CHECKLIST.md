# Owner Rotation Checklist — ParserNews P0 Gate

**Gate:** MERGE PR #6 / prod deploy **HOLD** until все строки ниже `revoked` или `rotated` / `not used`.  
**Правило:** не вставлять значения секретов в chat, PR, CI, логи, этот файл.

Статусы (единственные допустимые):

| Status | Meaning |
|--------|---------|
| `revoked` | Старый credential инвалидирован у провайдера |
| `rotated` | Новый credential выдан и прописан только в серверный/локальный `.env` (не в git) |
| `not used` | Credential не используется в ParserNews runtime |
| `pending Owner` | Ждёт действия Owner |

---

## Status matrix (репозиторий не может ротировать за Owner)

| Credential | Where was exposed | Owner action | Status |
|------------|-------------------|--------------|--------|
| Telegram Bot token (`TELEGRAM_BOT_TOKEN`) | tracked `.env` / git history | BotFather → Revoke → Issue new → update prod `.env` only | `pending Owner` |
| Second Telegram bot token (misleading key name if present in old `.env`) | tracked `.env` / history | BotFather → Revoke both if unknown which is live → keep one in prod | `pending Owner` |
| Telethon `TELEGRAM_API_ID_USER` / `TELEGRAM_API_HASH_USER` | tracked `.env` / history | my.telegram.org → revoke/regenerate app → new session files | `pending Owner` |
| Telethon `*.session` / `session_string.txt` | may exist locally / history | Delete old sessions; recreate after new API hash; never commit | `pending Owner` |
| OpenAI `OPENAI_API_KEY` | tracked `.env` / history | platform.openai.com → revoke key → create new → prod `.env` | `pending Owner` |
| YouTube `YOUTUBE_API_KEY` | tracked `.env` / history | Google Cloud Console → restrict/delete old key → new key | `pending Owner` |
| Google service-account (`credentials.json`) | tracked file / history | GCP IAM → disable/delete old key → create new JSON **only on server** | `pending Owner` |
| `MEDIA_UPLOAD_TOKEN` | tracked `.env` / history | Rotate on Site **and** Parser prod `.env` to same new value | `pending Owner` |
| `SITE_CACHE_INVALIDATE_TOKEN` (if separate) | env | Rotate with Site mirror or mark `not used` if alias of MEDIA token | `pending Owner` |
| Proxy `PROXY_USER` / `PROXY_PASS` / proxy URL with auth | tracked `.env` / history | Change proxy auth or mark `not used` if placeholder-only | `pending Owner` |

Owner заполняет колонку Status после действия (вручную в копии чеклиста или issue comment **без значений**).

---

## После rotation (Owner) — verify без значений

```powershell
# Локально / на сервере — только наличие переменных, без echo значений
python -c "import os; keys=['TELEGRAM_BOT_TOKEN','OPENAI_API_KEY','YOUTUBE_API_KEY','MEDIA_UPLOAD_TOKEN','TELEGRAM_API_HASH_USER'];
print({k: ('set' if os.getenv(k) else 'missing') for k in keys})"
```

Expected: `set` для используемых ключей; **не** печатать сами значения.

```bash
# Prod secrets files не в git
cd /opt/bot3/parser-new-bot
git ls-files .env credentials.json
# Expected: empty
test -f .env && echo env_file_present
test -f credentials.json && echo gcp_file_present
```

---

## Блокеры merge / deploy

1. Любая строка matrix остаётся `pending Owner` → **HOLD**.
2. History purge не выполнен и не согласован → secrets считаются скомпрометированными → rotation всё равно обязательна.
3. Force-push history rewrite — **только** отдельный Owner GO ([`HISTORY_PURGE_PLAN.md`](HISTORY_PURGE_PLAN.md)).
