import logging
from typing import List, Dict, Union
from collections import defaultdict
import functools

logger = logging.getLogger(__name__)

_cache = {}

def cache_metrics(func):
    """Декоратор для кэширования метрик на уровне сеанса."""
    @functools.wraps(func)
    def wrapper(news_items):
        # ключ кэша = количество новостей + id первого элемента
        if not news_items:
            return func(news_items)
        key = (len(news_items), id(news_items[0]))
        if key in _cache:
            logger.debug("⚡ Метрики взяты из кэша.")
            return _cache[key]
        result = func(news_items)
        _cache[key] = result
        return result
    return wrapper

@cache_metrics
def calculate_metrics(news_items: List[Dict[str, Union[str, List[str]]]]) -> Dict[str, Union[int, List]]:
    """Вычисляет основные метрики парсинга новостей."""
    if not news_items:
        logger.info("Нет новостей для анализа.")
        return {"total_news": 0, "unique_sources": 0, "word_count": 0, "top_sources": []}

    sources_count = defaultdict(int)
    word_count = 0
    for item in news_items:
        word_count += len(item.get("content", "").split())
        sources_count[item.get("source", "Неизвестный источник")] += 1

    metrics = {
        "total_news": len(news_items),
        "unique_sources": len(sources_count),
        "word_count": word_count,
        "top_sources": sorted(sources_count.items(), key=lambda x: x[1], reverse=True)[:5]
    }

    logger.info(f"Топ источников: {metrics['top_sources']}")
    return metrics
