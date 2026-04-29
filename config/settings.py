import importlib
import os

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

# Загружаем переменные окружения из файла .env
load_dotenv()


class Config:
    """Настройки приложения, загружаемые из переменных окружения."""

    # Telegram Bot API
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_API_ID_USER = os.getenv("TELEGRAM_API_ID_USER")
    TELEGRAM_API_HASH_USER = os.getenv("TELEGRAM_API_HASH_USER")
    TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")

    # Конфигурация OpenAI и NLP
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    TEXT_MODEL = os.getenv("TEXT_MODEL", "gpt-4o-mini")
    WHISPER_MODEL = os.getenv("WHISPER_MODEL", "gpt-4o-transcribe")
    IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gpt-image-1")
    NL_LANG = os.getenv("NL_LANG", "ru")

    # Пути и идентификаторы
    DB_PATH = os.getenv("DB_PATH", "data.db")
    CHANNEL_ID = os.getenv("CHANNEL_ID")
    EDITORS_CHAT_ID = os.getenv("EDITORS_CHAT_ID")
    OWNER_USER_ID = os.getenv("OWNER_USER_ID")

    # Интеграции Google/YouTube
    GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
    MEDIA_UPLOAD_URL = os.getenv("MEDIA_UPLOAD_URL")
    MEDIA_UPLOAD_TOKEN = os.getenv("MEDIA_UPLOAD_TOKEN")

    # Планировщик и интервалы задач
    COLLECT_INTERVAL_MINUTES = int(os.getenv("COLLECT_INTERVAL_MINUTES", "15"))
    NLP_INTERVAL_MINUTES = int(os.getenv("NLP_INTERVAL_MINUTES", "2"))
    RETRY_PUBLICATIONS_INTERVAL_MINUTES = int(
        os.getenv("RETRY_PUBLICATIONS_INTERVAL_MINUTES", "5")
    )
    DAILY_STATS_HOUR = int(os.getenv("DAILY_STATS_HOUR", "21"))
    DAILY_STATS_MINUTE = int(os.getenv("DAILY_STATS_MINUTE", "0"))
    SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "UTC")

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

    # Рабочие параметры
    MESSAGE_DELAY = 2
    MEDIA_DOWNLOAD_DELAY = 3
    MAX_MESSAGES = 100
    TELEGRAM_REQUESTS_PER_MINUTE = 30
    PARSING_INTERVAL = 3600

    # Настройки прокси
    PROXY_ENABLED = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    PROXY_TYPE = os.getenv("PROXY_TYPE", "socks5")
    PROXY_HOST = os.getenv("PROXY_HOST")
    PROXY_PORT = int(os.getenv("PROXY_PORT", 1080))
    PROXY_USER = os.getenv("PROXY_USER")
    PROXY_PASS = os.getenv("PROXY_PASS")


config = Config()
