import logging
from typing import List, Dict
from utils.logger import logger

def segment_news_by_source(news_items: List[Dict]) -> Dict[str, List]:
    """Разбивает новости на сегменты по источникам (Telegram, RSS, YouTube, Website)."""
    segmented_news = {
        "telegram": [],
        "rss": [],
        "youtube": [],
        "website": []
    }

    if not news_items:
        logger.info("Нет новостей для сегментации.")
        return segmented_news

    for item in news_items:
        source = item.get("source", "").lower()
        if "telegram" in source:
            segmented_news["telegram"].append(item)
        elif "rss" in source:
            segmented_news["rss"].append(item)
        elif "youtube" in source:
            segmented_news["youtube"].append(item)
        elif "website" in source:
            segmented_news["website"].append(item)
        else:
            logger.warning(f"Неопознанный источник в новости: {source}")

    logger.info(f"Сегментация завершена. Telegram: {len(segmented_news['telegram'])}, "
                f"RSS: {len(segmented_news['rss'])}, YouTube: {len(segmented_news['youtube'])}, "
                f"Websites: {len(segmented_news['website'])}")

    return segmented_news
