import feedparser
from bs4 import BeautifulSoup
import logging
from datetime import datetime, timedelta
import json
import hashlib

from utils.rss_media import collect_rss_entry_images

logger = logging.getLogger(__name__)


def _rss_tag_terms(entry) -> str:
    """Собрать теги RSS: feedparser отдаёт list[FeedParserDict], не list[str]."""
    tags = entry.get("tags") if hasattr(entry, "get") else getattr(entry, "tags", None)
    if not tags:
        return ""
    parts: list[str] = []
    for tag in tags:
        if isinstance(tag, str):
            term = tag.strip()
        elif isinstance(tag, dict):
            term = str(tag.get("term") or tag.get("label") or "").strip()
        else:
            term = str(getattr(tag, "term", None) or getattr(tag, "label", None) or "").strip()
        if term:
            parts.append(term)
    return ",".join(parts)


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
            content_blocks = entry.get('content') or []
            if content_blocks and isinstance(content_blocks[0], dict):
                content_html = content_blocks[0].get('value') or entry.get('summary', '') or ''
            else:
                content_html = entry.get('summary', '') or ''
            content_text = BeautifulSoup(content_html, 'html.parser').get_text(separator=' ', strip=True)
            raw_tags = _rss_tag_terms(entry)
            # Wakeboarding Mag и подобные: в feed нет enclosure — берём og:image со страницы.
            images = collect_rss_entry_images(entry, fetch_article_og=True, timeout=15.0)
            videos: list[str] = []
            for media in entry.get('media_content', []) or []:
                if not isinstance(media, dict):
                    continue
                url, mtype = media.get('url', '') or '', media.get('type', '') or ''
                if str(mtype).startswith('video') and url:
                    videos.append(url)
            checksum = hashlib.md5((title + source.url).encode('utf-8')).hexdigest()
            cover = images[0] if images else ""
            news_items.append({
                "id": entry_id or hashlib.md5((title+link).encode('utf-8')).hexdigest(),
                "source_type": "rss",
                "source_name": source.name,
                "source_url": source.url,
                "link": link,
                "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                "ingest_status": "raw",
                "raw_title": title or "(без заголовка)",
                "raw_content": content_text,
                "raw_html": content_html,
                "raw_media": json.dumps(images + videos),
                "cover_image_url": cover,
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
