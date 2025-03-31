import logging
import csv
import os
from datetime import datetime
from utils.logger import logger

REPORTS_DIR = "reports"
os.makedirs(REPORTS_DIR, exist_ok=True)  # Создаём папку для отчётов, если её нет

def generate_report(news_items, format="csv"):
    """Генерирует отчёт о собранных новостях в CSV или TXT."""
    if not news_items:
        logger.warning("Попытка создать отчёт с пустым списком новостей.")
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = os.path.join(REPORTS_DIR, f"news_report_{timestamp}.{format}")

    try:
        if format == "csv":
            with open(filename, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["Дата", "Источник", "Заголовок", "Содержание", "Ссылка"])
                for news in news_items:
                    writer.writerow([
                        news.get("date", ""),
                        news.get("source", ""),
                        news.get("title", ""),
                        news.get("content", ""),
                        news.get("link", "")
                    ])
        elif format == "txt":
            with open(filename, mode="w", encoding="utf-8") as file:
                for news in news_items:
                    file.write(f"Дата: {news.get('date', '')}\n")
                    file.write(f"Источник: {news.get('source', '')}\n")
                    file.write(f"Заголовок: {news.get('title', '')}\n")
                    file.write(f"Содержание: {news.get('content', '')}\n")
                    file.write(f"Ссылка: {news.get('link', '')}\n")
                    file.write("=" * 80 + "\n")
        else:
            logger.error(f"Неподдерживаемый формат отчёта: {format}")
            return None

        logger.info(f"Отчёт успешно сохранён: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Ошибка при создании отчёта: {e}")
        return None
