import logging
import os
from logging.handlers import TimedRotatingFileHandler

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Создаём папку logs, если её нет
os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("mywave")
logger.setLevel(LOG_LEVEL)
handler = TimedRotatingFileHandler("logs/mywave.log", when="midnight", interval=1, backupCount=7)
formatter = logging.Formatter('[%(asctime)s] %(levelname)s:%(name)s:%(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Также выводим в консоль
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

logger.info("Логгер mywave с ротацией и консолью инициализирован.")
