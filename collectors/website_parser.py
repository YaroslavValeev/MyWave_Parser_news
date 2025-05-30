import aiohttp
from selectolax.parser import HTMLParser
import logging
import random
import dateparser
from datetime import datetime, timedelta
import hashlib
import json

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0)",
]

async def fetch_site(session, url):
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    async with session.get(url, headers=headers) as resp:
        html = await resp.text()
        tree = HTMLParser(html)
        return tree

async def parse_website_async(source, filter_keywords):
    """
    Асинхронный парсинг сайта с помощью aiohttp + selectolax.
    source.url — адрес сайта
    filter_keywords — ключевые слова для фильтрации
    """
    logger.info(f"🌐 Начинаем асинхронный парсинг сайта {source.name} ({source.url})")
    news_items = []
    async with aiohttp.ClientSession() as session:
        try:
            tree = await fetch_site(session, source.url)
            last_id = getattr(source, 'last_id', None)
            new_top_id = None
            for article in tree.css('article'):
                title = article.css_first('h1,h2,h3')
                title = title.text(strip=True) if title else '(без заголовка)'
                link = article.css_first('a')
                link = link.attributes.get('href') if link else source.url
                if last_id and link == last_id:
                    break
                content = article.text(strip=True)
                if filter_keywords and getattr(source, 'filter', None):
                    text_for_filter = (title + ' ' + content).lower()
                    if not any(kw in text_for_filter for kw in filter_keywords):
                        continue
                keywords_list = []
                if filter_keywords:
                    combined_text = (title + ' ' + content).lower()
                    keywords_list = [kw for kw in filter_keywords if kw in combined_text]
                images = [img.attributes.get('src') for img in article.css('img') if img.attributes.get('src')]
                date_str = ""
                time_tag = article.css_first('time,span.date,div.date')
                if time_tag:
                    raw_date = time_tag.attributes.get('datetime', '') or time_tag.text(strip=True)
                    settings = {
                        'TIMEZONE': 'UTC',
                        'RETURN_AS_TIMEZONE_AWARE': True,
                        'DATE_ORDER': 'DMY'
                    }
                    parsed_date = dateparser.parse(raw_date, settings=settings)
                    if parsed_date:
                        date_str = parsed_date.strftime("%Y-%m-%d %H:%M:%S")
                if not date_str:
                    search_result = dateparser.search.search_dates(content)
                    if search_result:
                        date_str = search_result[0][1].strftime("%Y-%m-%d %H:%M:%S")
                checksum = hashlib.md5((title + source.url).encode('utf-8')).hexdigest()
                news_items.append({
                    "id": hashlib.md5((title+link).encode('utf-8')).hexdigest(),
                    "source_type": "site",
                    "source_name": source.name,
                    "source_url": source.url,
                    "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    "ingest_status": "raw",
                    "raw_title": title,
                    "raw_content": content,
                    "raw_html": str(article),
                    "raw_media": json.dumps(images),
                    "raw_tags": ','.join(keywords_list),
                    "checksum": checksum,
                    "parse_error": "",
                    "debug_info": f"site_link={link}"
                })
                if new_top_id is None and link:
                    new_top_id = link
            if new_top_id:
                source.last_id = new_top_id
        except Exception as e:
            logger.error(f"Ошибка загрузки или парсинга {source.url}: {e}")
            news_items.append({
                "id": "",
                "source_type": "site",
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
        dstr = item.get("created_at", "")
        if not dstr:
            filtered.append(item)
            continue
        try:
            dt = datetime.strptime(dstr[:19], "%Y-%m-%d %H:%M:%S")
            if dt >= two_months_ago:
                filtered.append(item)
        except Exception:
            filtered.append(item)
    logger.info(f"🌐 Найдено {len(news_items)} новостей с сайта {source.name}, из них за 2 месяца: {len(filtered)}")
    return list(reversed(filtered))
