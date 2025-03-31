import feedparser
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def parse_rss(source, filter_keywords):
    """
    Парсит RSS/Atom-ленту.
    :param source: Объект NewsSource с полями url, name, last_id, filter.
    :param filter_keywords: Список ключевых слов для фильтрации (lowercase).
    :return: Список словарей (news item dicts).
    """
    news_items = []
    try:
        feed = feedparser.parse(source.url)
        if not feed or not feed.entries:
            logger.warning(f"RSS: {source.name} - пустая или недоступная лента.")
            return []

        last_id = source.last_id
        new_top_id = None

        for entry in feed.entries:
            entry_id = entry.get('id', entry.get('link', ''))
            if last_id and entry_id == last_id:
                break

            title = entry.get('title', '').strip()
            link = entry.get('link', '').strip()
            content_html = entry.get('content', [{'value': entry.get('summary', '')}])[0]['value']
            content_text = BeautifulSoup(content_html, 'html.parser').get_text(separator=' ', strip=True)

            if filter_keywords and source.filter:
                text_for_filter = (title + ' ' + content_text).lower()
                if not any(kw in text_for_filter for kw in filter_keywords):
                    continue

            images, videos = [], []
            for media in entry.get('media_content', []):
                url, mtype = media.get('url', ''), media.get('type', '')
                if mtype.startswith('image'):
                    images.append(url)
                elif mtype.startswith('video'):
                    videos.append(url)

            date_str = entry.get('published', entry.get('updated', ''))

            news_items.append({
                "source": f"RSS: {source.name}",
                "title": title or "(без заголовка)",
                "content": content_text,
                "link": link,
                "date": date_str,
                "images": images,
                "videos": videos,
                "transcript": "",
                "comment": ""
            })

            if new_top_id is None:
                new_top_id = entry_id

        if new_top_id:
            source.last_id = new_top_id

    except Exception as e:
        logger.error(f"Ошибка парсинга RSS {source.url}: {e}", exc_info=True)

    # ====== ФИЛЬТРАЦИЯ ЗА ПОСЛЕДНИЕ 2 МЕСЯЦА =======
    two_months_ago = datetime.now() - timedelta(days=60)
    filtered = []
    for item in news_items:
        date_str = item.get("date", "")
        if not date_str:
            # Если нет даты — добавим
            filtered.append(item)
            continue
        # Попытаемся распарсить
        try:
            # Допустим, часто RSS: 'Mon, 06 Mar 2025 12:34:56 +0000'
            # Обрежем лишнее, либо используем datetime.strptime с подходящим форматом
            # Или можно использовать dateparser — на ваше усмотрение
            dt = date_str[:25]  # грубое обрезание
            parsed_date = datetime.strptime(dt, "%a, %d %b %Y %H:%M:%S")
            if parsed_date >= two_months_ago:
                filtered.append(item)
        except Exception:
            # Если формат необычный, не получилось — добавим
            filtered.append(item)

    logger.info(f"RSS: {source.name} -> после фильтрации за 2 месяца: {len(filtered)} из {len(news_items)}")
    return list(reversed(filtered))
