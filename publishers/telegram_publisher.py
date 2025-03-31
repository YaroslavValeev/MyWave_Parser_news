import logging
import html
from telegram import Bot, ParseMode, InputMediaPhoto, InputMediaVideo
import config.settings as config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

class TelegramPublisher:
    """Класс для публикации новостей в Telegram-канал."""

    def __init__(self, bot_token: str, chat_id: str):
        self.bot = Bot(bot_token)
        self.chat_id = chat_id

    def format_message(self, news_item):
        """Форматирует сообщение для отправки в Telegram."""
        title = html.escape(news_item.get("title", "Без заголовка"))
        content = html.escape(news_item.get("content", ""))
        link = news_item.get("link", "")

        message = f"<b>{title}</b>\n\n{content[:1000]}"
        if link:
            message += f"\n\n<a href='{link}'>Подробнее</a>"

        return message

    def send_news(self, news_item):
        """Отправляет новость в Telegram-канал с медиа (если есть)."""
        try:
            message = self.format_message(news_item)
            images = news_item.get("images", [])
            videos = news_item.get("videos", [])

            media_group = []
            if images:
                media_group.append(InputMediaPhoto(images[0], caption=message, parse_mode=ParseMode.HTML))
                media_group.extend(InputMediaPhoto(img) for img in images[1:10])
            if videos:
                media_group.append(InputMediaVideo(videos[0], caption=message, parse_mode=ParseMode.HTML))
                media_group.extend(InputMediaVideo(vid) for vid in videos[1:10])

            if media_group:
                self.bot.send_media_group(chat_id=self.chat_id, media=media_group[:10])
            else:
                self.bot.send_message(chat_id=self.chat_id, text=message, parse_mode=ParseMode.HTML)

            logger.info(f"Новость отправлена в Telegram: {news_item.get('title')}")
        except Exception as e:
            logger.error(f"Ошибка отправки новости в Telegram: {e}", exc_info=True)

# Пример использования
if __name__ == "__main__":
    publisher = TelegramPublisher(config.config.TELEGRAM_BOT_TOKEN, "@mywave_news")
    test_news = {
        "title": "Пример новости",
        "content": "Это тестовое сообщение для Telegram-бота.",
        "link": "https://example.com/news/1",
        "images": ["https://example.com/image.jpg"],
        "videos": []
    }
    publisher.send_news(test_news)
