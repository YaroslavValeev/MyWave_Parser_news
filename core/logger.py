import logging
import os
from logging.handlers import RotatingFileHandler

# Определяем уровень логирования (по умолчанию INFO)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Настройка логирования: запись в файл с ротацией и вывод в консоль
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s")

file_handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setFormatter(log_formatter)

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

# Создаём объект логгера
logger = logging.getLogger("MyWaveBot")
logger.setLevel(LOG_LEVEL)
logger.addHandler(file_handler)
logger.addHandler(console_handler)

logger.info("Логгер настроен и работает.")
