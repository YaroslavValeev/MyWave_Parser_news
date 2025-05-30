import feedparser
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timedelta
import json
import hashlib

logger = logging.getLogger(__name__)

def parse_rss(source, filter_keywords):
    """
    Парсит RSS/Atom-ленту и возвращает данные по структуре raw_feed.
    """
    news_items = []
    try:
        feed = feedparser.parse(source.url)
        if not feed or not feed.entries:
            logger.warning(f"RSS: {source.name} - пустая или недоступная лента.")
            return []

        last_id = getattr(source, 'last_id', None)
        new_top_id = None

        for entry in feed.entries:
            entry_id = entry.get('id', entry.get('link', ''))
            if last_id and entry_id == last_id:
                break

            title = entry.get('title', '').strip()
            link = entry.get('link', '').strip()
            content_html = entry.get('content', [{'value': entry.get('summary', '')}])[0]['value']
            content_text = BeautifulSoup(content_html, 'html.parser').get_text(separator=' ', strip=True)
            raw_tags = ','.join(entry.get('tags', [])) if 'tags' in entry else ''
            images, videos = [], []
            for media in entry.get('media_content', []):
                url, mtype = media.get('url', ''), media.get('type', '')
                if mtype.startswith('image'):
                    images.append(url)
                elif mtype.startswith('video'):
                    videos.append(url)
            date_str = entry.get('published', entry.get('updated', ''))
            # Формируем checksum по raw_title+source_url
            checksum = hashlib.md5((title + source.url).encode('utf-8')).hexdigest()
            news_items.append({
                "id": entry_id or hashlib.md5((title+link).encode('utf-8')).hexdigest(),
                "source_type": "rss",
                "source_name": source.name,
                "source_url": source.url,
                "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                "ingest_status": "raw",
                "raw_title": title or "(без заголовка)",
                "raw_content": content_text,
                "raw_html": content_html,
                "raw_media": json.dumps(images + videos),
                "raw_tags": raw_tags,
                "checksum": checksum,
                "parse_error": "",
                "debug_info": f"rss_link={link}"
            })
            if new_top_id is None:
                new_top_id = entry_id
        if new_top_id:
            source.last_id = new_top_id
    except Exception as e:
        logger.error(f"Ошибка парсинга RSS {source.url}: {e}", exc_info=True)
        news_items.append({
            "id": "",
            "source_type": "rss",
            "source_name": getattr(source, 'name', ''),
            "source_url": getattr(source, 'url', ''),
            "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            "ingest_status": "error",
            "raw_title": "",
            "raw_content": "",
            "raw_html": "",
            "raw_media": "[]",
            "raw_tags": "",
            "checksum": "",
            "parse_error": str(e),
            "debug_info": ""
        })
    # Фильтрация за последние 2 месяца
    two_months_ago = datetime.now() - timedelta(days=60)
    filtered = []
    for item in news_items:
        date_str = item.get("created_at", "")
        if not date_str:
            filtered.append(item)
            continue
        try:
            dt = datetime.strptime(date_str[:19], "%Y-%m-%d %H:%M:%S")
            if dt >= two_months_ago:
                filtered.append(item)
        except Exception:
            filtered.append(item)
    logger.info(f"RSS: {source.name} -> после фильтрации за 2 месяца: {len(filtered)} из {len(news_items)}")
    return list(reversed(filtered))
