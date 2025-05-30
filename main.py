import logging
from collectors.rss_collector import fetch_rss
from collectors.youtube_parser import fetch_youtube
from collectors.telegram_collector import fetch_telegram
from processors.deduplication import add_checksum, is_duplicate
from services.google_sheets import GoogleSheets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    try:
        logger.info("Инициализация Google Sheets...")
        sheets = GoogleSheets("credentials.json", "1RJpw2mAMej3a-VC6yKAsKkVQvzGStcjUC7LijNNyn50")
        existing_ids = sheets.get_existing_ids()
        existing_checksums = sheets.get_existing_checksums()

        rss_sources = [
            {"name": "Wakeboarding Magazine", "url": "https://www.wakeboardingmag.com/feed"},
            {"name": "Alliance Wake", "url": "https://alliancewake.com/feed/"},
            {"name": "World Wake Association Blog", "url": "https://www.thewwa.com/feed/"},
            {"name": "Unleashed Wake Magazine", "url": "https://unleashedwakemag.com/feed/"},
            {"name": "Make A Wake Marine Blog", "url": "https://makeawakemarine.com/blogs/make-a-wake-marine-blog/feed"},
            {"name": "Miami Ski Nautique Blog", "url": "https://blog.miamiskinautiques.com/feed"},
            {"name": "Wakeboarding RSS Feed", "url": "https://rss.feedspot.com/wakeboarding_rss"}
        ]

        new_items = []
        for src in rss_sources:
            logger.info(f"Парсинг RSS: {src['name']} ({src['url']})")
            news = fetch_rss(src["url"], src["name"])
            for item in news:
                item = add_checksum(item)
                if item.id in existing_ids or is_duplicate(item, existing_checksums):
                    continue
                new_items.append(item)

        youtube_sources = [
            {"name": "JB ONeill", "url": "https://www.youtube.com/channel/UCJluNGyCB"},
            {"name": "Shaun Murray", "url": "https://www.youtube.com/channel/UCEO3Li9O6"},
            {"name": "David O'Caoimh", "url": "https://www.youtube.com/channel/UCbc8Ap_hq"},
            {"name": "IWWF WORLD CUP", "url": "https://www.youtube.com/channel/UCVpeKZf-T"}
        ]
        for src in youtube_sources:
            logger.info(f"Парсинг YouTube: {src['name']} ({src['url']})")
            news = fetch_youtube(src["url"], src["name"])
            for item in news:
                item = add_checksum(item)
                if item.id in existing_ids or is_duplicate(item, existing_checksums):
                    continue
                new_items.append(item)

        telegram_channels = [
            "https://t.me/talktofish",
            "https://t.me/prowakesurf",
            "https://t.me/wakestyleclub",
            "https://t.me/moscow_wakesurfing",
            "https://t.me/waketime_msk",
            "https://t.me/wakediary",
            "https://t.me/wakedivision",
            "https://t.me/Privat_Wakesurfing",
            "https://t.me/russian_waterski",
            "https://t.me/RFSurf",
            "https://t.me/surfinmoscow",
            "https://t.me/atcc_russia",
            "https://t.me/surfmosobl",
            "https://t.me/s/wakeflot?after=571"
        ]
        for url in telegram_channels:
            logger.info(f"Парсинг Telegram: {url}")
            news = fetch_telegram(url)
            for item in news:
                item = add_checksum(item)
                if item.id in existing_ids or is_duplicate(item, existing_checksums):
                    continue
                new_items.append(item)

        if new_items:
            sheets.append_news_batch(new_items)
            logger.info(f"Всего добавлено {len(new_items)} новых новостей.")
        else:
            logger.info("Нет новых новостей для добавления.")
    except Exception as e:
        logger.error(f"Ошибка в main: {e}")


if __name__ == "__main__":
    main()
