from telethon import TelegramClient
import logging
from datetime import datetime, timedelta
from core.models import SourceItem
from core.models import NewsItem
import asyncio
from start_telethon import start_telethon, close_telethon

logger = logging.getLogger(__name__)

async def collect_telegram_news(client: TelegramClient, source, filter_keywords):
    """Собирает новости из Telegram-канала (пример)."""
    news_items = []
    # Use timezone-aware UTC for comparisons
    from datetime import timezone as _tz
    two_months_ago = datetime.now(_tz.utc) - timedelta(days=60)

    try:
        entity = await client.get_entity(source.url)
        async for message in client.iter_messages(entity, limit=50):
            text = message.text or ""
            if filter_keywords and source.filter:
                if not any(kw in text.lower() for kw in filter_keywords):
                    continue

            date_dt = None
            if message.date:
                # Normalize message.date to timezone-aware UTC
                msg_dt = message.date
                if msg_dt.tzinfo is None:
                    # assume UTC for naive datetimes
                    msg_dt = msg_dt.replace(tzinfo=_tz.utc)
                else:
                    # convert to UTC
                    msg_dt = msg_dt.astimezone(_tz.utc)
                date_dt = msg_dt

            images = []
            videos = []
            if message.media and hasattr(message.media, 'file_id'):
                # В реальности bot-аккаунт не может тут всё
                pass

            # Заполняем
            news_date_str = date_dt.strftime("%Y-%m-%d %H:%M:%S") if date_dt else ""
            item = {
                "source": f"Telegram: {source.name}",
                "title": text[:100] + ("..." if len(text) > 100 else ""),
                "content": text,
                "link": f"https://t.me/{source.url.split('/')[-1]}/{message.id}",
                "date": news_date_str,
                "images": images,
                "videos": videos,
                "transcript": "",
                "comment": ""
            }
            # Фильтрация за 2 месяца
            if date_dt and date_dt >= two_months_ago:
                news_items.append(item)

    except Exception as e:
        logger.error(f"Ошибка парсинга Telegram {source.url}: {e}", exc_info=True)

    # Reverse
    return news_items[::-1]


def fetch_telegram(url: str, name: str = ""):
    """Compatibility wrapper used by main.py. Attempts to initialize a
    Telethon client briefly and collect messages. If Telethon cannot be
    initialized in this context, returns an empty list and logs a warning.
    """
    src = SourceItem(id=url, name=name or url, url=url, type="telegram")
    try:
        # Try to initialize telethon client synchronously using the async helper
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        client = loop.run_until_complete(start_telethon())
        if client is None:
            logging.getLogger(__name__).warning("Telethon client not available; returning empty result from fetch_telegram")
            return []
        try:
            raw = loop.run_until_complete(collect_telegram_news(client, src, None))
            results = []
            for r in raw:
                try:
                    ni = NewsItem(
                        id=r.get('id') or r.get('link') or '',
                        source_type=r.get('source_type', 'telegram'),
                        source_name=r.get('source_name', src.name if hasattr(src, 'name') else ''),
                        source_url=r.get('source_url', src.url if hasattr(src, 'url') else url),
                        created_at=r.get('created_at', ''),
                        ingest_status=r.get('ingest_status', 'raw'),
                        raw_title=r.get('raw_title', r.get('title', '')),
                        raw_content=r.get('raw_content', r.get('content', '')),
                        raw_html=r.get('raw_html', ''),
                        raw_media=r.get('raw_media', ''),
                        lang=r.get('lang', ''),
                        raw_tags=r.get('raw_tags', ''),
                        checksum=r.get('checksum', ''),
                        parse_error=r.get('parse_error', ''),
                        debug_info=r.get('debug_info', ''),
                    )
                    results.append(ni)
                except Exception:
                    continue
            return results
        finally:
            loop.run_until_complete(close_telethon(client))
    except Exception as e:
        logging.getLogger(__name__).error(f"fetch_telegram wrapper failed: {e}")
        return []
