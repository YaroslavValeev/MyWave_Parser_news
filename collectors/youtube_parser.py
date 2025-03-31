import feedparser
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
import logging
import time
from datetime import datetime, timedelta
from collections import deque
from urllib.parse import urlparse

from collectors.telegram_parser import BaseParser
from utils.helpers import RateLimiter

logger = logging.getLogger(__name__)

MAX_RETRIES = 3  # максимальное количество повторов
BASE_DELAY = 2   # базовая задержка (секунд)
MAX_REQUESTS_PER_MINUTE = 60  # максимальное количество запросов в минуту

class YoutubeParser(BaseParser):
    def __init__(self, max_requests_per_minute):
        self.max_requests = max_requests_per_minute
        self.requests_timestamps = deque()
    
    def wait_if_needed(self):
        now = datetime.now()
        
        # Удаляем устаревшие метки времени (старше 1 минуты)
        while self.requests_timestamps and (now - self.requests_timestamps[0]).total_seconds() > 60:
            self.requests_timestamps.popleft()
        
        # Если достигли лимита, ждём
        if len(self.requests_timestamps) >= self.max_requests:
            sleep_time = 60 - (now - self.requests_timestamps[0]).total_seconds()
            if sleep_time > 0:
                logger.info(f"Достигнут лимит запросов. Ожидание {sleep_time:.1f} секунд...")
                time.sleep(sleep_time)
        
        self.requests_timestamps.append(now)

rate_limiter = RateLimiter(MAX_REQUESTS_PER_MINUTE)

def parse_youtube(source, filter_keywords):
    """
    Парсит YouTube RSS-ленту, получает транскрипт видео.
    :param source: Объект NewsSource с полями url, name, last_id, filter.
    :param filter_keywords: Список ключевых слов (lowercase).
    :return: Список словарей (news item dicts).
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

            last_id = source.last_id
            new_top_id = None

            for entry in feed.entries:
                link = entry.get('link', '').strip()
                if last_id and link == last_id:
                    break

                title = entry.get('title', '').strip()
                desc_html = entry.get('media_description', entry.get('summary', ''))
                desc_text = BeautifulSoup(desc_html, 'html.parser').get_text(separator=' ', strip=True)

                if filter_keywords and source.filter:
                    text_for_filter = (title + ' ' + desc_text).lower()
                    if not any(kw in text_for_filter for kw in filter_keywords):
                        continue

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
                        rate_limiter.wait_if_needed()
                        # Сначала проверяем доступность транскрипта
                        available_transcripts = YouTubeTranscriptApi.list_transcripts(video_id)
                        
                        # Пробуем получить русский или английский транскрипт
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
                            logger.info(f"Транскрипт недоступен для видео {video_id} (нет ru/en)")
                            transcript_text = "(Транскрипт недоступен)"
                            
                    except Exception as e:
                        if "TranscriptsDisabled" in str(e):
                            logger.info(f"Транскрипты отключены для видео {video_id}")
                        else:
                            logger.warning(f"Ошибка получения транскрипта для {video_id}: {str(e)}")
                        transcript_text = "(Транскрипт недоступен)"

                images = [thumb.get('url', '') for thumb in entry.get('media_thumbnail', [])]
                videos = [link] if link else []

                date_str = entry.get('published', entry.get('updated', ''))

                news_items.append({
                    "source": f"YouTube: {source.name}",
                    "title": title,
                    "content": desc_text,
                    "link": link,
                    "date": date_str,
                    "images": images,
                    "videos": videos,
                    "transcript": transcript_text,
                    "comment": ""
                })

                if new_top_id is None:
                    new_top_id = link

            if new_top_id:
                source.last_id = new_top_id

            # Если успешно спарсилось — выходим
            break

        except Exception as e:
            retry_count += 1
            logger.error(f"parse_youtube: ошибка при парсинге {source.url}: {e}", exc_info=True)
            # Экспоненциальный бэкофф
            delay = BASE_DELAY * (2 ** (retry_count - 1))
            logger.info(f"Повторная попытка через {delay} секунд...")
            time.sleep(delay)

    # ====== ФИЛЬТРАЦИЯ ЗА 2 МЕСЯЦА ======
    two_months_ago = datetime.now() - timedelta(days=60)
    filtered = []
    for item in news_items:
        dstr = item.get("date", "")
        if not dstr:
            filtered.append(item)
            continue
        try:
            # Часто в RSS YouTube формат: 'Tue, 07 Mar 2025 03:25:18 +0000'
            dt_short = dstr[:25]
            dt_obj = datetime.strptime(dt_short, "%a, %d %b %Y %H:%M:%S")
            if dt_obj >= two_months_ago:
                filtered.append(item)
        except Exception:
            filtered.append(item)
        try:
            url = source.url.lower().strip()

            if "youtube.com/feeds/videos.xml" in url:
                return url  # Уже правильный RSS

            if "youtube.com/channel/" in url:
                channel_id = url.split("/channel/")[-1].split("?")[0]
            elif "youtube.com/user/" in url:
                user_id = url.split("/user/")[-1].split("?")[0]
                return f"https://www.youtube.com/feeds/videos.xml?user={user_id}"
            elif "youtube.com/" in url:
                channel_id = url.split("/")[-1]
            else:
                raise ValueError("Неподдерживаемый формат URL")

            return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

        except Exception as e:
            logger.error(f"Ошибка обработки YouTube URL: {str(e)}")
            raise ValueError("Некорректный URL YouTube") from e

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
