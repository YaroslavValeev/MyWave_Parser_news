# Паттерн: Telegram + OpenAI через сервер вне РФ (EU SOCKS5)

Документ для команд MyWave и смежных проектов.

**Формула:** prod-приложение на RU VPS; исходящие вызовы OpenAI и Telegram — через dedicated EU VPS (SOCKS5); Site / Sheets / БД — напрямую, без прокси.

**Статус в Parser News:** path B принят (prod smoke PASS).

---

## 1. Проблема

На VPS в РФ типичны ограничения и нестабильность:

| Сервис | Симптом без выхода «за рубеж» |
|--------|-------------------------------|
| **OpenAI API** | `403 unsupported_country` / региональные блоки |
| **Telegram** (Bot API, Telethon / MTProto) | таймауты к `api.telegram.org`, `ProxyConnectionError` |

VPN «на ноутбуке разработчика» для prod не подходит: бот и парсер работают 24/7 на сервере.

---

## 2. Решение: двухузловая схема

```text
┌─────────────────────────────┐      SOCKS5 :1080       ┌──────────────────────────────┐
│  RU VPS (application)       │ ─────────────────────► │  EU VPS (proxy only)          │
│  bot / parser / systemd     │   только с IP RU VPS   │  3proxy, без бизнес-логики    │
│  пример: 62.113.42.227      │                        │  пример: 72.56.99.214         │
└─────────────────────────────┘                        └──────────────────────────────┘
           │                                                         │
           │ Site API, Google Sheets — напрямую                      │ OpenAI, Telegram API
           ▼                                                         ▼
     mywavewake.ru / Sheets                                    api.openai.com / Telegram
```

| Узел | Роль | Что держит |
|------|------|------------|
| **RU VPS** | Application / prod | Код, `.env` с API keys, SQLite/Sheets, systemd |
| **EU VPS** | Egress gateway | Только SOCKS5 (3proxy), auth, UFW |

EU VPS — **не** второй экземпляр бота. Это шлюз наружу для заблокированных API.

---

## 3. EU VPS: SOCKS5-прокси

### 3.1. Софт

- **3proxy** (на Ubuntu 24.04 пакет в apt может отсутствовать — сборка из исходников + `libssl-dev`)
- Порт: **1080**
- Auth: логин/пароль SOCKS-пользователя
- Отдельные пользователи под сервисы (пример: `parser`, `project2`)

### 3.2. Безопасность (обязательно)

```text
UFW: allow 1080/tcp from <RU_VPS_IP>
# НЕ открывать 0.0.0.0/0
```

- EU **не** хранит `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN` — только проксирует TCP
- Секреты прокси — только в prod `.env`, не в git
- При смене IP RU VPS — сразу обновить UFW на EU

### 3.3. Минимальный чеклист на EU

1. Поднять VPS вне РФ (Timeweb Cloud EU / Hetzner / аналог), статический IPv4.
2. Установить 3proxy, SOCKS5 + auth, порт 1080.
3. UFW: `1080` только с IP prod.
4. Проверка с RU: `nc -zv <EU_IP> 1080`.

---

## 4. RU VPS: переменные окружения

### 4.1. Общий SOCKS (Telethon + опционально Bot API)

```env
PROXY_ENABLED=true
PROXY_TYPE=socks5
PROXY_HOST=<EU_IP>
PROXY_PORT=1080
PROXY_USER=parser
PROXY_PASS=<secret>
BOT_API_USE_PROXY=true
```

### 4.2. OpenAI — отдельная переменная

HTTP-клиент OpenAI **не** читает `PROXY_*` автоматически; нужен явный URL:

```env
OPENAI_HTTP_PROXY=socks5://parser:<secret>@<EU_IP>:1080
```

Зависимость: пакет `socksio` / `httpx[socks]` (для `httpx` + SOCKS).

**Обязательно:**

1. Собирать URL из тех же `PROXY_USER` / `PROXY_PASS` / `PROXY_HOST` / `PROXY_PORT`, что для Telethon.
2. Scheme только `socks5://` (не `socks5h://` — `python_socks` / часть клиентов его не принимают).
3. Экранировать спецсимволы пароля (`@ : / # %`) через `urllib.parse.quote`.
4. После смены пароля на EU (3proxy) — **пересобрать** `OPENAI_HTTP_PROXY`, иначе SOCKS ответит `Connection not allowed by ruleset`.

Пример безопасной пересборки на RU (пароль не печатается):

```bash
cd /opt/bot3/parser-new-bot
source venv/bin/activate
python3 - <<'PY'
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
import os
load_dotenv()
user, pwd = os.environ["PROXY_USER"], os.environ["PROXY_PASS"]
host, port = os.environ["PROXY_HOST"], os.environ["PROXY_PORT"]
url = f"socks5://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}:{port}"
path = Path(".env")
lines = path.read_text(encoding="utf-8").splitlines()
out, found = [], False
for line in lines:
    if line.startswith("OPENAI_HTTP_PROXY="):
        out.append("OPENAI_HTTP_PROXY=" + url); found = True
    else:
        out.append(line)
if not found:
    out.append("OPENAI_HTTP_PROXY=" + url)
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print("UPDATED", f"{host}:{port}", "user=", user)
PY
```

Для YouTube Atom / RSS с RU VPS задайте тот же SOCKS:

```env
HTTP_FEED_PROXY=socks5://parser:<secret>@<EU_IP>:1080
HTTP_YOUTUBE_FEED_PROXY=socks5://parser:<secret>@<EU_IP>:1080
```

### 4.3. Dev на Windows (опционально)

Если удалённый EU недоступен с ноутбука — локальный Clash / v2rayN:

```env
# BOT_API_PROXY_URL=socks5://127.0.0.1:7891
# BOT_API_PROXY_FALLBACK_URLS=socks5://127.0.0.1:7890,socks5://127.0.0.1:7891
```

Шаблон: `.env.example`.

---

## 5. Как это подключено в Parser News (reference)

| Компонент | Переменные | Код |
|-----------|------------|-----|
| OpenAI (NLP, Whisper) | `OPENAI_HTTP_PROXY` | `nlp/openai_client.py` — `httpx.AsyncClient` (`proxies=` / `proxy=`) |
| Telethon (каналы, медиа) | `PROXY_*` | `bot.py`, `utils/telegram_session.py` |
| aiogram Bot API | `PROXY_*` / `BOT_API_PROXY_URL` | `bot_aiogram.py` — цепочка прокси + fallback |
| Site upload / Sheets | — | **без** EU proxy |

---

## 6. Smoke после настройки (RU VPS)

```bash
# 1) Доступность EU proxy
nc -zv <EU_IP> 1080

# 2) OpenAI через proxy (без печати ключа)
cd /opt/bot3/parser-new-bot   # путь prod — из SERVER_DEPLOY_CANON
source venv/bin/activate
python3 -c "
from dotenv import load_dotenv
load_dotenv()
import os, httpx
proxy = os.environ['OPENAI_HTTP_PROXY']
# минимальный TCP через SOCKS к api.openai.com — или вызов models.list с ключом
print('OPENAI_HTTP_PROXY set:', bool(proxy))
"

# 3) Сервис бота
sudo systemctl status parser-news-bot
journalctl -u parser-news-bot -n 50 --no-pager
```

**PASS:**

- OpenAI: не `403 unsupported_country`
- Telegram: long poll / Telethon без постоянных `ProxyConnectionError`
- EU: порт 1080 закрыт для мира, кроме IP RU VPS

---

## 7. Что проксировать, а что нет

| Трафик | Через EU SOCKS? |
|--------|-----------------|
| OpenAI API | **Да** |
| Telegram Bot API / Telethon | **Да** (если RU-сеть нестабильна) |
| Site `mywavewake.ru` (upload, cache) | **Нет** |
| Google Sheets | **Нет** |
| Локальная БД / диск | **Нет** |

Проксировать всё подряд — лишняя latency и лишняя зависимость от EU.

---

## 8. Риски и эксплуатация

| Риск | Митигация |
|------|-----------|
| EU VPS — SPOF для OpenAI/TG | Мониторинг 1080, алерт, запасной EU или fallback |
| Утечка SOCKS-пароля | Отдельные users на сервис; ротация; UFW по IP |
| Смена IP RU | Checklist: UFW на EU в тот же день |
| Два места настройки | Checklist: и `PROXY_*`, и `OPENAI_HTTP_PROXY` (пересобирать URL после смены пароля) |
| `OPENAI_HTTP_PROXY` с устаревшим паролем | Симптом: `Connection not allowed by ruleset`; фикс: quote + rebuild из `PROXY_*` |
| `httpx==0.24.1` + код с `proxy=` | TypeError; в requirements — `httpx[socks]>=0.28.1,<0.29` + helper `_httpx_proxy_client_kwargs` |

**Не коммитить** `.env` с `PROXY_PASS` / `OPENAI_HTTP_PROXY` с паролем.

---

## 9. Чеклист для другой команды (копировать)

1. Поднять **EU VPS**, статический IPv4.
2. Установить **3proxy** (SOCKS5 + auth, порт 1080).
3. **UFW:** `1080` only from prod IP.
4. На **RU prod** `.env`: `PROXY_*` + `OPENAI_HTTP_PROXY=socks5://...`.
5. Код: HTTP-клиент OpenAI читает proxy; Telethon/aiogram — `PROXY_*`.
6. Smoke: OpenAI + Telegram getMe / long poll.
7. **Не** проксировать Site / DB / Sheets без нужды.
8. Документировать IP/роль узлов для ops (без секретов в git).

---

## 10. Одна фраза для README / handoff

> Prod на RU VPS; исходящие OpenAI и Telegram — через dedicated EU SOCKS5 (доступ только с IP prod); Site и Sheets — напрямую.

---

## Связанные файлы (этот репозиторий)

- `.env.example` — `OPENAI_HTTP_PROXY`, `PROXY_*`, `BOT_API_*`
- `config/settings.py` — загрузка proxy-настроек
- `nlp/openai_client.py` — OpenAI via httpx proxy
- `utils/telegram_session.py` — Telethon proxy
- `bot_aiogram.py` — Bot API proxy chain
- `docs/SERVER_DEPLOY_CANON_RU.md` — prod paths (если есть)

**Версия:** 1.0  
**Владелец паттерна:** Parser News / TGbotAdmin (ops)
