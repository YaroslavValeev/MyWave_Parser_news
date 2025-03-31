import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import logging
import random
import dateparser
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0)",
]

PROXIES = [
    # Пример: 'http://user:password@ip:port'
    # Список можно хранить в .env
]

def get_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

def parse_website(source, filter_keywords):
    """
    Парсит веб-страницу.
    :param source: Объект NewsSource (url, name, last_id, filter).
    :param filter_keywords: Список ключевых слов (lowercase).
    :return: Список словарей (news item dicts).
    """
    logger.info(f"🌐 Начинаем парсинг сайта {source.name} ({source.url})")
    news_items = []

    headers = {"User-Agent": random.choice(USER_AGENTS)}
    proxy = random.choice(PROXIES) if PROXIES else None
    proxies = {"http": proxy, "https": proxy} if proxy else None

    try:
        session = get_session()
        response = session.get(source.url, timeout=15, headers=headers, proxies=proxies)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Ошибка загрузки {source.url}: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    last_id = source.last_id
    new_top_id = None

    items = soup.find_all('article')
    if not items:
        items = soup.find_all(lambda tag: tag.name in ['div', 'li']
                              and tag.get('class')
                              and any(sub in ' '.join(tag.get('class')) for sub in ['news', 'post']))
    for item in items:
        title_tag = item.find(['h1', 'h2', 'h3'])
        title = title_tag.get_text(strip=True) if title_tag else "(без заголовка)"
        link = urljoin(source.url, title_tag.find('a')['href']) if title_tag and title_tag.find('a') else source.url

        if last_id and link == last_id:
            break

        content_text = ' '.join(p.get_text(strip=True) for p in item.find_all('p'))
        if filter_keywords and source.filter:
            text_for_filter = (title + ' ' + content_text).lower()
            if not any(kw in text_for_filter for kw in filter_keywords):
                continue

        # Логика извлечения ключевых слов
        keywords_list = []
        if filter_keywords:
            combined_text = (title + ' ' + content_text).lower()
            keywords_list = [kw for kw in filter_keywords if kw in combined_text]

        images = [urljoin(source.url, img['src']) for img in item.find_all('img', src=True)]
        date_str = ""
        time_tag = item.find(['time', 'span.date', 'div.date'])
        if time_tag:
            raw_date = time_tag.get('datetime', '') or time_tag.get_text(strip=True)
            settings = {
                'TIMEZONE': 'UTC',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'DATE_ORDER': 'DMY'
            }
            parsed_date = dateparser.parse(raw_date, settings=settings)
            if parsed_date:
                date_str = parsed_date.strftime("%Y-%m-%d %H:%M:%S")
        # Фолбэк: пытаемся найти дату в тексте новости, если не найдено
        if not date_str:
            search_result = dateparser.search.search_dates(content_text)
            if search_result:
                date_str = search_result[0][1].strftime("%Y-%m-%d %H:%M:%S")

        news_items.append({
            "source": f"Website: {source.name}",
            "title": title,
            "content": content_text,
            "link": link,
            "date": date_str,
            "images": images,
            "videos": [],
            "transcript": "",
            "comment": "",
            "keywords": keywords_list
        })

        if new_top_id is None and link:
            new_top_id = link

    if new_top_id:
        source.last_id = new_top_id

    # ====== ФИЛЬТРАЦИЯ ЗА ПОСЛЕДНИЕ 2 МЕСЯЦА =======
    two_months_ago = datetime.now() - timedelta(days=60)
    filtered = []
    for item in news_items:
        dstr = item.get("date", "")
        if not dstr:
            filtered.append(item)
            continue
        try:
            dt = datetime.strptime(dstr, "%Y-%m-%d %H:%M:%S")
            if dt >= two_months_ago:
                filtered.append(item)
        except Exception:
            filtered.append(item)

    logger.info(f"🌐 Найдено {len(news_items)} новостей с сайта {source.name}, из них за 2 месяца: {len(filtered)}")
    return list(reversed(filtered))
