# Fix: бот не отвечает в Telegram (Proxy timeout)

**Симптом:** команды `/stats`, `/collect_group_authors` без ответа в чате.  
**Причина (prod, 2026-11-06):** Bot API ходит через EU SOCKS `PROXY_HOST:PROXY_PORT`, прокси **не отвечает** → `ProxyTimeoutError` / `TelegramNetworkError`. Unit падает и рестартится (restart counter сотни).  
**Код деплоя в порядке:** SHA `6d9afa6`, команда `collect_group_authors` есть. Без живого SOCKS ответов в TG не будет.

Сервер приложения: `root@62.113.42.227`  
Каталог: `/opt/bot3/parser-new-bot`  
EU proxy (канон): `72.56.99.214:1080` — см. `docs/EU_PROXY_PATTERN_RU.md`

---

## A) Диагностика на RU VPS (вставить целиком)

```bash
cd /opt/bot3/parser-new-bot
date
systemctl is-active parser-news-bot
git rev-parse HEAD
# ожидаемо: 6d9afa692585587520acb0db0e24a5528a977597

# TCP до EU SOCKS (без пароля)
PROXY_HOST=$(grep -E '^PROXY_HOST=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
PROXY_PORT=$(grep -E '^PROXY_PORT=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
echo "PROXY_HOST=$PROXY_HOST PROXY_PORT=$PROXY_PORT"
timeout 8 bash -c "echo >/dev/tcp/${PROXY_HOST}/${PROXY_PORT}" \
  && echo proxy_tcp_OK || echo proxy_tcp_FAIL

# Прямой HTTPS к Telegram (на RU часто FAIL — это нормально)
timeout 12 curl -sS -o /dev/null -w "direct_api_telegram=%{http_code} time=%{time_total}\n" \
  https://api.telegram.org || echo direct_api_FAIL

journalctl -u parser-news-bot -n 40 --no-pager | egrep -i 'ProxyTimeout|TelegramNetwork|Start polling|proxy=' || true
systemctl show parser-news-bot -p NRestarts --value
```

Если `proxy_tcp_FAIL` — чинить EU-прокси (блок B), не код бота.

---

## B) Починить EU SOCKS (на машине прокси, обычно 72.56.99.214)

```bash
# SSH на EU VPS
ssh root@72.56.99.214

# сервис 3proxy / socks — имена могут отличаться:
systemctl status 3proxy --no-pager || systemctl status socks5 --no-pager || true
ss -lntup | egrep ':1080\b' || true
ufw status | head -n 40

# UFW: порт 1080 только с IP RU VPS
ufw allow from 62.113.42.227 to any port 1080 proto tcp
ufw reload

# если сервис упал:
systemctl restart 3proxy || systemctl restart socks5 || true
ss -lntup | egrep ':1080\b'
```

Проверка **с RU** снова:

```bash
timeout 8 bash -c "echo >/dev/tcp/72.56.99.214/1080" && echo proxy_tcp_OK || echo proxy_tcp_FAIL
```

---

## C) После живого SOCKS — перезапуск бота на RU

```bash
cd /opt/bot3/parser-new-bot
systemctl restart parser-news-bot
sleep 3
systemctl is-active parser-news-bot
journalctl -u parser-news-bot -n 30 --no-pager | egrep -i 'Bot API session|Start polling|ProxyTimeout|ERROR' || true
```

Ожидаемо в логе:
- `Bot API session: proxy=...:1080 (socks5)`
- `Start polling`
- **нет** свежих `ProxyTimeoutError`

Проверка getMe (секреты не печатает целиком):

```bash
cd /opt/bot3/parser-new-bot
venv/bin/python - <<'PY'
import asyncio, os
from dotenv import load_dotenv
load_dotenv('.env')
from bot_aiogram import _build_bot_session
from aiogram import Bot

async def main():
    bot = Bot(token=os.environ['TELEGRAM_BOT_TOKEN'], session=_build_bot_session())
    me = await bot.get_me()
    print('getMe_ok', me.username, me.id)
    await bot.session.close()
asyncio.run(main())
PY
```

---

## D) Smoke в Telegram

В личке `@MyWaveParcer_bot` / «MyWave ньюзмейкер»:

```text
/stats
/collect_group_authors @wakedivision
```

Логи во время smoke:

```bash
journalctl -u parser-news-bot -f --no-pager | egrep -i 'stats|collect_group|ProxyTimeout|TelegramNetwork|ERROR'
```

---

## E) Если EU IP сменился — обновить .env на RU (без печати пароля)

```bash
cd /opt/bot3/parser-new-bot
# посмотреть текущие ключи (значения HOST замаскировать вручную)
grep -E '^(PROXY_ENABLED|PROXY_TYPE|PROXY_HOST|PROXY_PORT|BOT_API_USE_PROXY)=' .env

# пример смены HOST (подставьте новый EU IP):
# sed -i 's/^PROXY_HOST=.*/PROXY_HOST=NEW.EU.IP.HERE/' .env
# systemctl restart parser-news-bot
```

`BOT_API_USE_PROXY=true` оставлять **включённым** на RU VPS — без SOCKS ответы в Telegram обычно не доходят.

---

## F) Экстренный rollback кода (не лечит прокси)

```bash
cd /opt/bot3/parser-new-bot
systemctl stop parser-news-bot
git checkout --force 06fcfb43dda9ee5ac9139ed8bf043bed9f25f651
systemctl start parser-news-bot
```
