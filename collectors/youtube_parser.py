import feedparser
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
import logging
import time
from datetime import datetime, timedelta
from collections import deque
from urllib.parse import urlparse
import json
import hashlib

from collectors.telegram_parser import BaseParser
from utils.helpers import RateLimiter

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 2
MAX_REQUESTS_PER_MINUTE = 60

class YoutubeParser(BaseParser):
    def __init__(self, max_requests_per_minute):
        self.max_requests = max_requests_per_minute
        self.requests_timestamps = deque()
    
    def wait_if_needed(self):
        now = datetime.now()
        while self.requests_timestamps and (now - self.requests_timestamps[0]).total_seconds() > 60:
            self.requests_timestamps.popleft()
        if len(self.requests_timestamps) >= self.max_requests:
            sleep_time = 60 - (now - self.requests_timestamps[0]).total_seconds()
            if sleep_time > 0:
                logger.info(f"Достигнут лимит запросов. Ожидание {sleep_time:.1f} секунд...")
                time.sleep(sleep_time)
        self.requests_timestamps.append(now)

rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)

def parse_youtube(source, filter_keywords):
    """
    Парсит YouTube RSS-ленту и возвращает данные по структуре raw_feed.
    """
    logger.info(f"📺 Начинаем парсинг YouTube канала {source.name} ({source.url})")
    news_items = []
    retry_count = 0
    while retry_count < MAX_RETRIES:
        try:
            feed = feedparser.parse(source.url)
            if not feed or not feed.entries:
                logger.warning(f"YouTube: {source.name} - пустая или недоступная лента.")
                return []
            last_id = getattr(source, 'last_id', None)
            new_top_id = None
            for entry in feed.entries:
                link = entry.get('link', '').strip()
                if last_id and link == last_id:
                    break
                title = entry.get('title', '').strip()
                desc_html = entry.get('media_description', entry.get('summary', ''))
                desc_text = BeautifulSoup(desc_html, 'html.parser').get_text(separator=' ', strip=True)
                video_id = None
                if 'watch?v=' in link:
                    video_id = link.split('watch?v=')[-1].split('&')[0]
                elif 'youtu.be/' in link:
                    video_id = link.split('youtu.be/')[-1].split('?')[0]
                else:
                    video_id = entry.get('yt_videoid', '')
                transcript_text = ""
                if video_id:
                    try:
                        available_transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
                        transcript = None
                        for lang in ['ru', 'en']:
                            try:
                                transcript = available_transcripts.find_transcript([lang])
                                break
                            except:
                                continue
                        if transcript:
                            transcript_data = transcript.fetch()
                            transcript_text = ' '.join([seg['text'] for seg in transcript_data])
                        else:
                            transcript_text = "(Транскрипт недоступен)"
                    except Exception as e:
                        transcript_text = "(Транскрипт недоступен)"
                images = [thumb.get('url', '') for thumb in entry.get('media_thumbnail', [])]
                videos = [link] if link else []
                date_str = entry.get('published', entry.get('updated', ''))
                checksum = hashlib.md5((title + source.url).encode('utf-8')).hexdigest()
                news_items.append({
                    "id": hashlib.md5((title+link).encode('utf-8')).hexdigest(),
                    "source_type": "youtube",
                    "source_name": source.name,
                    "source_url": source.url,
                    "created_at": datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    "ingest_status": "raw",
                    "raw_title": title,
                    "raw_content": desc_text + "\n" + transcript_text,
                    "raw_html": desc_html,
                    "raw_media": json.dumps(images + videos),
                    "raw_tags": "",
                    "checksum": checksum,
                    "parse_error": "",
                    "debug_info": f"yt_link={link}"
                })
                if new_top_id is None:
                    new_top_id = link
            if new_top_id:
                source.last_id = new_top_id
            break
        except Exception as e:
            retry_count += 1
            logger.error(f"parse_youtube: ошибка при парсинге {source.url}: {e}", exc_info=True)
            time.sleep(BASE_DELAY * (2 ** (retry_count - 1)))
            news_items.append({
                "id": "",
                "source_type": "youtube",
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
            dt_short = dstr[:19]
            dt_obj = datetime.strptime(dt_short, "%Y-%m-%d %H:%M:%S")
            if dt_obj >= two_months_ago:
                filtered.append(item)
        except Exception:
            filtered.append(item)
    logger.info(f"📺 Найдено {len(news_items)} видео из канала {source.name}, из них за 2 месяца: {len(filtered)}")
    return list(reversed(filtered))

def validate_source(self, source):
        """
        Проверяет корректность источника YouTube перед парсингом.
        Извлекает channel_id из URL, если он не указан.
        """
        if not hasattr(source, 'channel_id') or not source.channel_id:
            # Извлекаем channel_id из URL, если его нет
            parsed = urlparse(source.url)
            if parsed.netloc == 'www.youtube.com':
                path_parts = parsed.path.split('/')
                if len(path_parts) >= 2:
                    source.channel_id = path_parts[1]
                else:
                    raise ValueError(f"Некорректный URL YouTube: {source.url}")
            elif parsed.netloc == 'youtube.com':
                path_parts = parsed.path.split('/')
                if len(path_parts) >= 3 and path_parts[1] == 'channel':
                    source.channel_id = path_parts[2]
                else:
                    raise ValueError(f"Некорректный URL YouTube: {source.url}")
            elif parsed.netloc == 'youtu.be':
                source.channel_id = parsed.path.split('/')[-1]
            else:
                raise ValueError(f"Некорректный URL YouTube: {source.url}")
        super().validate_source(source)
