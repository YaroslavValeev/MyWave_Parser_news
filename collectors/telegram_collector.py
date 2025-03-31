from telethon import TelegramClient
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

async def collect_telegram_news(client: TelegramClient, source, filter_keywords):
    """Собирает новости из Telegram-канала (пример)."""
    news_items = []
    two_months_ago = datetime.now() - timedelta(days=60)

    try:
        entity = await client.get_entity(source.url)
        async for message in client.iter_messages(entity, limit=50):
            text = message.text or ""
            if filter_keywords and source.filter:
                if not any(kw in text.lower() for kw in filter_keywords):
                    continue

            date_dt = None
            if message.date:
                date_dt = message.date

            images = []
            videos = []
            if message.media and hasattr(message.media, 'file_id'):
                # В реальности bot-аккаунт не может тут всё
                pass

            # Заполняем
            news_date_str = message.date.strftime("%Y-%m-%d %H:%M:%S")
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
