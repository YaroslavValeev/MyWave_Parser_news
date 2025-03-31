import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

class Config:
    # Telegram Bot API
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")  # Токен бота
    TELEGRAM_API_ID_USER = os.getenv("TELEGRAM_API_ID_USER")  # API ID пользователя
    TELEGRAM_API_HASH_USER = os.getenv("TELEGRAM_API_HASH_USER")  # API Hash пользователя
    TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")  # Номер телефона
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Ключ API OpenAI
    GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")  # Файл с учетными данными Google
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")  # ID Google Sheets
    YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
    # Остальные настройки...
    MESSAGE_DELAY = 2  # Задержка между сообщениями (в секундах)
    MEDIA_DOWNLOAD_DELAY = 3 # Задержка между загрузками медиа (в секундах)
    MAX_MESSAGES = 100 # Максимальное количество сообщений
    TELEGRAM_REQUESTS_PER_MINUTE = 30 # Количество запросов в минуту
    PARSING_INTERVAL = 3600 # Интервал парсинга в секундах
     # Новые настройки прокси
    PROXY_ENABLED = os.getenv("PROXY_ENABLED", "False").lower() == "true"
    PROXY_TYPE = os.getenv("PROXY_TYPE", "socks5")
    PROXY_HOST = os.getenv("PROXY_HOST")
    PROXY_PORT = int(os.getenv("PROXY_PORT", 1080))
    PROXY_USER = os.getenv("PROXY_USER")
    PROXY_PASS = os.getenv("PROXY_PASS")


config = Config()