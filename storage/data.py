import sqlite3
import os
from datetime import datetime, timedelta
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.getcwd(), "data.db")

def init_db():
    """Инициализация базы данных (создание таблицы новостей, если её нет)."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS news (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    title TEXT,
                    content TEXT,
                    link TEXT UNIQUE,
                    date TEXT,
                    images TEXT,
                    videos TEXT,
                    transcript TEXT,
                    comment TEXT
                )
            ''')
            conn.commit()
            logger.info("База данных успешно инициализирована.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}", exc_info=True)

def save_news(news_items):
    """Сохранение списка новостей в базу данных с использованием пакетных операций."""
    if not news_items:
        logger.info("Список новостей пуст, сохранение не требуется.")
        return

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            data_to_insert = []
            for item in news_items:
                # Проверка на дубликат по link
                cursor.execute("SELECT link FROM news WHERE link = ?", (item.get("link", ""),))
                if cursor.fetchone() is None:
                    data_to_insert.append(
                        (
                            item.get("source", ""),
                            item.get("title", ""),
                            item.get("content", ""),
                            item.get("link", ""),
                            item.get("date", ""),
                            "\n".join(item.get("images", [])),
                            "\n".join(item.get("videos", [])),
                            item.get("transcript", ""),
                            item.get("comment", "")
                        )
                    )
            if data_to_insert:
                cursor.executemany('''
                    INSERT INTO news (source, title, content, link, date, images, videos, transcript, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', data_to_insert)
                conn.commit()
                logger.info(f"Успешно сохранено {len(data_to_insert)} новостей.")
            else:
                logger.info("Нет новых новостей для сохранения.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при сохранении новостей: {e}", exc_info=True)

def get_latest_news(limit=10, as_dict=True):
    """Получение последних новостей из базы данных."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row if as_dict else None
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM news ORDER BY id DESC LIMIT ?', (limit,))
            return [dict(row) for row in cursor.fetchall()] if as_dict else cursor.fetchall()
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении новостей: {e}", exc_info=True)
        return []

def clear_old_news(days=30):
    """Удаление новостей, старше N дней."""
    try:
        threshold_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM news WHERE date < ?", (threshold_date,))
            conn.commit()
            logger.info(f"Удалены новости старше {days} дней.")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении старых новостей: {e}", exc_info=True)

# Инициализация БД при запуске
init_db()
