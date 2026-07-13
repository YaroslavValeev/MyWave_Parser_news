# Secret Rotation Runbook (ParserNews)

**Статус:** обязателен до любого prod deploy после обнаружения tracked `.env` / `credentials.json` в публичном git.  
**Правило:** не вставлять значения секретов в чат, PR, логи или этот документ.

## Findings (без значений)

| Артефакт | Severity | Действие |
|----------|----------|----------|
| `.env` был в git index | P0 | untrack + rotation всех ключей |
| `credentials.json` (GCP SA) был в git | P0 | revoke key в GCP IAM |
| История публичного репо | P0 | history purge (`git filter-repo` / BFG) — Owner |
| CI без secret scan | P1 | gitleaks job в `.github/workflows/ci.yml` |

## Credentials к ротации (Owner checklist)

- [ ] `TELEGRAM_BOT_TOKEN` (BotFather → revoke + новый)
- [ ] Второй Telegram bot token (если был в `.env` под другим именем)
- [ ] `OPENAI_API_KEY`
- [ ] `YOUTUBE_API_KEY`
- [ ] `MEDIA_UPLOAD_TOKEN` (+ зеркало на Site `/var/www/mywave/.env`)
- [ ] `SITE_CACHE_INVALIDATE_TOKEN` (если отдельный)
- [ ] `TELEGRAM_API_ID_USER` / `TELEGRAM_API_HASH_USER` + Telethon `*.session`
- [ ] GCP service account key (`credentials.json`) — create new key, disable old
- [ ] Proxy credentials (если не placeholder)

## Локальный cleanup

```powershell
# Репозиторий не должен трекать секреты
git ls-files .env credentials.json
# Ожидание: пусто

# .env остаётся только локально / на сервере
Copy-Item .env.example .env   # затем заполнить новыми значениями вручную
```

## History purge (Owner, после rotation)

```bash
# Пример — только после backup и согласования Owner
git filter-repo --path .env --invert-paths
git filter-repo --path credentials.json --invert-paths
# force-push согласован отдельно
```

## Verify

- [ ] `.env` и `credentials.json` в `.gitignore`
- [ ] `.env.example` без реальных значений
- [ ] CI `secret-scan` job зелёный
- [ ] Prod `.env` обновлён, сервис перезапущен
- [ ] Старые ключи инвалидированы у провайдеров
