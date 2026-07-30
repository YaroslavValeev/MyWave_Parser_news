import importlib
import os
from pathlib import Path
from urllib.parse import quote, urlparse

# Корень репозитория (рядом с .env), не зависит от текущей рабочей директории при запуске.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"

# Some environments or installed packages can shadow or modify the
# standard-library "importlib" module. Import importlib.util directly
# when available, and fall back gracefully if it's not present.
try:
    from importlib import util as _importlib_util  # type: ignore
except Exception:
    _importlib_util = getattr(importlib, "util", None)

if _importlib_util is not None:
    _dotenv_spec = _importlib_util.find_spec("dotenv")
else:
    _dotenv_spec = None

if _dotenv_spec is not None:
    _dotenv_module = importlib.import_module("dotenv")
    load_dotenv = getattr(_dotenv_module, "load_dotenv")
else:
    def load_dotenv(*args, **kwargs):  # type: ignore[override]
        return False

# Загружаем .env из корня проекта; override=True — иначе пустая CHANNEL_ID из среды Windows
# перекрывает значение из файла (типичная причина «задал в .env, а бот не видит»).
if _ENV_FILE.is_file():
    load_dotenv(_ENV_FILE, override=True)
else:
    load_dotenv()

# Путь к использованному .env (для логов при старте бота, без секретов)
ENV_FILE_USED = str(_ENV_FILE.resolve()) if _ENV_FILE.is_file() else ""
ENV_FILE_EXPECTED = str(_ENV_FILE.resolve())


class Config:
    """Настройки приложения, загружаемые из переменных окружения."""

    # Telegram Bot API
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_API_ID_USER = os.getenv("TELEGRAM_API_ID_USER")
    TELEGRAM_API_HASH_USER = os.getenv("TELEGRAM_API_HASH_USER")
    TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

    # Конфигурация OpenAI и NLP
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    # HTTP(S) прокси для OpenAI API (Timeweb/РФ: без прокси часто 403 unsupported_country)
    OPENAI_HTTP_PROXY = (os.getenv("OPENAI_HTTP_PROXY") or os.getenv("HTTP_OPENAI_PROXY") or "").strip() or None
    TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-4o-mini")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")
    NL_LANG = os.getenv("NL_LANG", "ru")
    # Явно не вызывать Moderations API (если 403 или не нужна проверка)
    OPENAI_SKIP_MODERATION = os.getenv("OPENAI_SKIP_MODERATION", "false").lower() == "true"

    # Пути и идентификаторы
    DB_PATH = os.getenv("DB_PATH", "data.db")
    FSM_STORAGE_BACKEND = os.getenv("FSM_STORAGE_BACKEND", "sqlite").strip().lower()
    FSM_SQLITE_PATH = os.getenv("FSM_SQLITE_PATH", "data/fsm_state.db")
    FSM_STATE_TTL_SECONDS = int(os.getenv("FSM_STATE_TTL_SECONDS", str(24 * 60 * 60)))
    # Целевой chat_id для публикации: личный чат, группа или канал.
    # CHANNEL_ID и TELEGRAM_CHANNEL_ID имеют одинаковый смысл.
    CHANNEL_ID = (os.getenv("CHANNEL_ID") or os.getenv("TELEGRAM_CHANNEL_ID") or "").strip() or None
    EDITORS_CHAT_ID = (os.getenv("EDITORS_CHAT_ID") or "").strip() or None
    OWNER_USER_ID = os.getenv("OWNER_USER_ID")
    # Ссылка на отзывы MyWave (Яндекс.Карты); пусто — блок не показывается
    YANDEX_REVIEW_URL = (os.getenv("YANDEX_REVIEW_URL") or "").strip() or None
    # Футер публикации в канал: «сайт» / «тг-админ»
    PUBLICATION_SITE_URL = (
        os.getenv("PUBLICATION_SITE_URL") or os.getenv("SITE_BASE_URL") or "https://mywavewake.ru/"
    ).strip()
    PUBLICATION_ADMIN_BOT_URL = (
        os.getenv("PUBLICATION_ADMIN_BOT_URL") or "https://t.me/MyWave_Admin_bot"
    ).strip()
    # true = OpenAI переписывает комментарий Owner; false = саммари + почти сырой комментарий
    OWNER_POST_USE_LLM_REWRITE = os.getenv("OWNER_POST_USE_LLM_REWRITE", "false").lower() == "true"

    # Интеграции Google/YouTube
    GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
    # Лист бегущей строки соревнований (контракт Site_MyWave competitions_ticker v1)
    COMPETITIONS_SHEET_NAME = os.getenv("COMPETITIONS_SHEET_NAME", "competitions_ticker")
    COMPETITIONS_SYNC_ENABLED = os.getenv("COMPETITIONS_SYNC_ENABLED", "true").lower() == "true"
    COMPETITIONS_ARCHIVE_HOUR = int(os.getenv("COMPETITIONS_ARCHIVE_HOUR", "3"))
    COMPETITIONS_ARCHIVE_MINUTE = int(os.getenv("COMPETITIONS_ARCHIVE_MINUTE", "15"))
    COMPETITIONS_CACHE_INVALIDATE_ENDPOINT = (
        os.getenv("COMPETITIONS_CACHE_INVALIDATE_ENDPOINT") or "/api/competitions/cache/invalidate"
    ).strip()
    COMPETITIONS_CACHE_INVALIDATE_TOKEN = (
        os.getenv("COMPETITIONS_CACHE_INVALIDATE_TOKEN") or ""
    ).strip()
    COMPETITIONS_CACHE_INVALIDATE_TIMEOUT_SECONDS = float(
        os.getenv("COMPETITIONS_CACHE_INVALIDATE_TIMEOUT_SECONDS", "15")
    )
    # Сбор комментариев под постами Telegram-каналов → channel_commenters + user_messages
    ENGAGEMENT_COLLECT_ENABLED = (
        os.getenv("ENGAGEMENT_COLLECT_ENABLED", "false").lower() == "true"
    )
    ENGAGEMENT_POSTS_LIMIT = int(os.getenv("ENGAGEMENT_POSTS_LIMIT", "15"))
    ENGAGEMENT_COMMENTS_PER_POST = int(os.getenv("ENGAGEMENT_COMMENTS_PER_POST", "50"))
    ENGAGEMENT_CHANNELS_CHUNK = int(os.getenv("ENGAGEMENT_CHANNELS_CHUNK", "2"))
    ENGAGEMENT_CRON_HOUR = int(os.getenv("ENGAGEMENT_CRON_HOUR", "4"))
    ENGAGEMENT_CRON_MINUTE = int(os.getenv("ENGAGEMENT_CRON_MINUTE", "30"))
    USER_MESSAGES_SHEET_NAME = os.getenv("USER_MESSAGES_SHEET_NAME", "user_messages")
    # Автосбор календарей соревнований (фаза C)
    COMPETITIONS_COLLECT_ENABLED = (
        os.getenv("COMPETITIONS_COLLECT_ENABLED", "false").lower() == "true"
    )
    COMPETITIONS_IWWF_CALENDAR_URL = (
        os.getenv("COMPETITIONS_IWWF_CALENDAR_URL") or "https://www.iwwf.sport/"
    ).strip()
    COMPETITIONS_WSWS_CALENDAR_URL = (
        os.getenv("COMPETITIONS_WSWS_CALENDAR_URL") or "https://www.worldwakeassociation.com/"
    ).strip()
    # WWA Tribe Events iCal (webcal → https при загрузке)
    COMPETITIONS_WWA_ICAL_URL = (
        os.getenv("COMPETITIONS_WWA_ICAL_URL")
        or "webcal://www.thewwa.com/?post_type=tribe_events&ical=1&eventDisplay=list"
    ).strip()
    # CWSA (Canadian Wakesurf Association) — HTML-календарь
    COMPETITIONS_CWSA_CALENDAR_URL = (
        os.getenv("COMPETITIONS_CWSA_CALENDAR_URL") or "https://thecwsa.org/events/2026"
    ).strip()
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

    # Публичная база для локально сохранённых медиа, которые должны попасть в Google Sheets.
    # Если файл сохранён как downloads/review_media/x.jpg, то при PUBLIC_MEDIA_BASE_URL=https://site.ru
    # в raw_feed уйдёт https://site.ru/static/downloads/review_media/x.jpg.
    # Без этой настройки локальные пути и /static/... не пишутся в raw_feed как cover/image.
    PUBLIC_MEDIA_BASE_URL = (os.getenv("PUBLIC_MEDIA_BASE_URL") or "").strip().rstrip("/")
    ALLOW_RELATIVE_STATIC_MEDIA_IN_RAW_FEED = (
        os.getenv(
            "ALLOW_RELATIVE_STATIC_MEDIA_IN_RAW_FEED",
            os.getenv("RAW_FEED_ALLOW_RELATIVE_STATIC_MEDIA", "false"),
        ).lower()
        == "true"
    )
    SITE_BASE_URL = (os.getenv("SITE_BASE_URL") or os.getenv("MYWAVE_SITE_BASE_URL") or "").strip().rstrip("/")
    MEDIA_UPLOAD_URL = (
        os.getenv("MEDIA_UPLOAD_URL")
        or os.getenv("SITE_MEDIA_UPLOAD_URL")
        or ""
    ).strip()
    MEDIA_UPLOAD_ENDPOINT = (
        os.getenv("MEDIA_UPLOAD_ENDPOINT")
        or os.getenv("SITE_MEDIA_UPLOAD_ENDPOINT")
        or "/api/blog/media/upload"
    ).strip()
    MEDIA_UPLOAD_TOKEN = (
        os.getenv("MEDIA_UPLOAD_TOKEN")
        or os.getenv("SITE_MEDIA_UPLOAD_TOKEN")
        or os.getenv("MYWAVE_MEDIA_UPLOAD_TOKEN")
        or ""
    ).strip()
    MEDIA_UPLOAD_TIMEOUT_SECONDS = float(os.getenv("MEDIA_UPLOAD_TIMEOUT_SECONDS", "60"))
    MEDIA_UPLOAD_MAX_BYTES = int(os.getenv("MEDIA_UPLOAD_MAX_BYTES", str(10 * 1024 * 1024)))
    SITE_CACHE_INVALIDATE_ENDPOINT = (
        os.getenv("SITE_CACHE_INVALIDATE_ENDPOINT")
        or "/api/blog/cache/invalidate"
    ).strip()
    SITE_CACHE_INVALIDATE_TOKEN = (
        os.getenv("SITE_CACHE_INVALIDATE_TOKEN")
        or os.getenv("MEDIA_UPLOAD_TOKEN")
        or os.getenv("SITE_MEDIA_UPLOAD_TOKEN")
        or os.getenv("MYWAVE_MEDIA_UPLOAD_TOKEN")
        or ""
    ).strip()
    SITE_CACHE_INVALIDATE_TIMEOUT_SECONDS = float(
        os.getenv("SITE_CACHE_INVALIDATE_TIMEOUT_SECONDS", "15")
    )

    # Планировщик и интервалы задач
    # По умолчанию сбор выполняется раз в сутки в 12:00 по Москве.
    # Для старого интервального режима задайте COLLECT_SCHEDULE_MODE=interval.
    COLLECT_SCHEDULE_MODE = os.getenv("COLLECT_SCHEDULE_MODE", "daily").strip().lower()
    COLLECT_INTERVAL_MINUTES = int(os.getenv("COLLECT_INTERVAL_MINUTES", "30"))
    COLLECT_DAILY_HOUR = int(os.getenv("COLLECT_DAILY_HOUR", "12"))
    COLLECT_DAILY_MINUTE = int(os.getenv("COLLECT_DAILY_MINUTE", "0"))
    COLLECT_MISFIRE_GRACE_SECONDS = int(
        os.getenv("COLLECT_MISFIRE_GRACE_SECONDS", str(12 * 60 * 60))
    )
    NLP_INTERVAL_MINUTES = int(os.getenv("NLP_INTERVAL_MINUTES", "2"))
    # 0 = каждый тик обходятся все источники; N>0 — за тик только N источников (round-robin).
    COLLECT_SOURCES_CHUNK_SIZE = int(os.getenv("COLLECT_SOURCES_CHUNK_SIZE", "0"))
    # Лимит команды /requeue_nlp (владелец): анти-спам OpenAI
    REQUEUE_NLP_COOLDOWN_SECONDS = int(os.getenv("REQUEUE_NLP_COOLDOWN_SECONDS", "60"))
    REQUEUE_NLP_MAX_PER_HOUR = int(os.getenv("REQUEUE_NLP_MAX_PER_HOUR", "20"))
    RETRY_PUBLICATIONS_INTERVAL_MINUTES = int(
        os.getenv("RETRY_PUBLICATIONS_INTERVAL_MINUTES", "5")
    )
    DAILY_STATS_HOUR = int(os.getenv("DAILY_STATS_HOUR", "21"))
    DAILY_STATS_MINUTE = int(os.getenv("DAILY_STATS_MINUTE", "0"))
    SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Europe/Moscow")

    # Пакетный экспорт owner_* из SQLite logs → лист admin_actions__review (и CSV при сбое Sheets)
    OWNER_AUDIT_EXPORT_ENABLED = (
        os.getenv("OWNER_AUDIT_EXPORT_ENABLED", "false").lower() == "true"
    )
    OWNER_AUDIT_EXPORT_INTERVAL_MINUTES = int(
        os.getenv("OWNER_AUDIT_EXPORT_INTERVAL_MINUTES", "60")
    )
    ADMIN_ACTIONS_SHEET_NAME = os.getenv("ADMIN_ACTIONS_SHEET_NAME", "admin_actions__review")

    def _parse_retry_minutes(self) -> list[int]:
        raw = os.getenv("PUBLICATION_RETRY_MINUTES")
        if not raw:
            return [5, 15, 60]
        minutes: list[int] = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                minutes.append(max(0, int(float(chunk))))
            except ValueError:
                continue
        return minutes or [5, 15, 60]

    PUBLICATION_RETRY_MINUTES = property(lambda self: self._parse_retry_minutes())
    PUBLICATION_MAX_ATTEMPTS = int(os.getenv("PUBLICATION_MAX_ATTEMPTS", "3"))
    PUBLICATION_IMMEDIATE_RETRIES = int(os.getenv("PUBLICATION_IMMEDIATE_RETRIES", "2"))
    PUBLICATION_IMMEDIATE_DELAY_SECONDS = float(
        os.getenv("PUBLICATION_IMMEDIATE_DELAY_SECONDS", "1")
    )
    # Автоматически не публиковать старый хвост publish_retry спустя несколько суток.
    # 0 — без ограничения по возрасту.
    PUBLICATION_RETRY_MAX_AGE_HOURS = float(
        os.getenv("PUBLICATION_RETRY_MAX_AGE_HOURS", "72")
    )
    # Не показывать в ревью материалы, опубликованные раньше N дней назад (0 = без фильтра).
    REVIEW_MAX_AGE_DAYS = int(os.getenv("REVIEW_MAX_AGE_DAYS", "30"))

    # Рабочие параметры
    MESSAGE_DELAY = 2
    MEDIA_DOWNLOAD_DELAY = 3
    MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "2"))
    # Полный сбор: пропустить скачивание файлов Telegram.
    # Если включено, cover_image_url будет пустым вместо t.me permalink.
    # По умолчанию медиа качаем (важно для отображения). Если сбор слишком долгий —
    # выставьте TELEGRAM_SKIP_MEDIA_FULL_COLLECT=true (ссылки t.me на посты всё равно пишутся).
    TELEGRAM_SKIP_MEDIA_FULL_COLLECT = (
        os.getenv("TELEGRAM_SKIP_MEDIA_FULL_COLLECT", "false").lower() == "true"
    )
    # Legacy-флаг для старого импортера. Новый TelethonParser пишет в raw_feed только прямые
    # media refs (/static/...) и никогда не использует t.me permalink как image_url.
    TELEGRAM_ARCHIVE_MEDIA_TO_DISK = (
        os.getenv("TELEGRAM_ARCHIVE_MEDIA_TO_DISK", "false").lower() == "true"
    )
    # Один файл медиа через Telethon; без лимита зависание может длиться очень долго.
    TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS = float(
        os.getenv("TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", "90")
    )
    # Circuit breaker: при подряд ошибках скачивания медиа (сеть и т.п.) временно не качаем файлы.
    TELEGRAM_MEDIA_CB_FAILURE_THRESHOLD = int(
        os.getenv("TELEGRAM_MEDIA_CB_FAILURE_THRESHOLD", "5")
    )
    TELEGRAM_MEDIA_CB_COOLDOWN_SECONDS = int(
        os.getenv("TELEGRAM_MEDIA_CB_COOLDOWN_SECONDS", "300")
    )
    TELEGRAM_REQUESTS_PER_MINUTE = 30
    PARSING_INTERVAL = 3600
    # Таймаут HTTP при загрузке страниц сайтов (секунды), см. utils/import_asyncio.
    WEBSITE_REQUEST_TIMEOUT = float(os.getenv("WEBSITE_REQUEST_TIMEOUT", "60"))

    # Настройки прокси
    PROXY_ENABLED = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    PROXY_TYPE = os.getenv("PROXY_TYPE", "socks5")
    PROXY_HOST = os.getenv("PROXY_HOST")
    PROXY_PORT = int(os.getenv("PROXY_PORT", 1080))
    PROXY_USER = os.getenv("PROXY_USER")
    PROXY_PASS = os.getenv("PROXY_PASS")
    # Bot API (aiogram) через тот же SOCKS: по умолчанию да, если PROXY_ENABLED.
    # False — ходить на api.telegram.org напрямую, пока Telethon может использовать PROXY_*.
    # Полезно, если прокси жив для MTProto, но даёт таймаут на HTTPS к Bot API.
    BOT_API_USE_PROXY = os.getenv("BOT_API_USE_PROXY", "true").lower() == "true"
    # Отдельный прокси только для Bot API (aiogram), например локальный SOCKS VPN: socks5://127.0.0.1:7891
    BOT_API_PROXY_URL = (os.getenv("BOT_API_PROXY_URL") or "").strip() or None
    # Резервные прокси для bot_aiogram (через запятую), если основной PROXY_* недоступен
    # (типично: socks5://127.0.0.1:7890 для Clash).
    BOT_API_PROXY_FALLBACK_URLS = (os.getenv("BOT_API_PROXY_FALLBACK_URLS") or "").strip()
    # Таймаут HTTP к api.telegram.org (сек); на нестабильной сети / Windows поднимайте (121 / семафор).
    BOT_API_REQUEST_TIMEOUT = float(os.getenv("BOT_API_REQUEST_TIMEOUT", "120"))
    # TTL DNS-кэша коннектора aiohttp (сек) для прямого TCP к api.telegram.org
    BOT_API_CONNECTOR_DNS_TTL = int(os.getenv("BOT_API_CONNECTOR_DNS_TTL", "300"))
    # Если PROXY_HOST / BOT_API_PROXY_URL указывают на удалённый узел, bot_aiogram перебирает локальные SOCKS (7890…)
    BOT_API_TRY_LOCAL_SOCKS = os.getenv("BOT_API_TRY_LOCAL_SOCKS", "true").lower() == "true"

    def bot_api_dedicated_proxy_url(self) -> str | None:
        """Полный URL прокси только для редактора (минует BOT_API_USE_PROXY / общий PROXY_*)."""
        return self.BOT_API_PROXY_URL

    def bot_proxy_endpoint_hint(self) -> str:
        """Краткая подпись прокси для логов (без пароля)."""
        raw = self.BOT_API_PROXY_URL
        if raw:
            p = urlparse(raw)
            host = p.hostname or "?"
            port = p.port
            if port is None:
                port = 1080
            sch = (p.scheme or "?").lower()
            return f"{host}:{port} ({sch})"
        if self.PROXY_HOST:
            return f"{self.PROXY_HOST}:{self.PROXY_PORT} ({(self.PROXY_TYPE or 'socks5').lower()})"
        return "не задан"

    def proxy_url_from_env(self) -> str | None:
        """URL прокси из PROXY_* при PROXY_ENABLED (тот же прокси, что для Telethon)."""
        if not self.PROXY_ENABLED or not self.PROXY_HOST:
            return None
        host = str(self.PROXY_HOST).strip()
        if not host:
            return None
        ptype = (self.PROXY_TYPE or "socks5").strip().lower()
        port = int(self.PROXY_PORT)
        user, pwd = self.PROXY_USER, self.PROXY_PASS
        if user and pwd:
            return (
                f"{ptype}://{quote(str(user), safe='')}:"
                f"{quote(str(pwd), safe='')}@{host}:{port}"
            )
        return f"{ptype}://{host}:{port}"

    def bot_api_proxy_url(self) -> str | None:
        """URL прокси для aiogram (Bot API), если явно разрешено BOT_API_USE_PROXY."""
        if not self.BOT_API_USE_PROXY:
            return None
        return self.proxy_url_from_env()

    def bot_api_proxy_fallback_urls(self) -> list[str]:
        if not self.BOT_API_PROXY_FALLBACK_URLS:
            return []
        return [x.strip() for x in self.BOT_API_PROXY_FALLBACK_URLS.split(",") if x.strip()]


config = Config()
