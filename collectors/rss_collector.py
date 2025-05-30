import feedparser
from core.models import NewsItem
from datetime import datetime

def fetch_rss(rss_url, source_name):
    feed = feedparser.parse(rss_url)
    news_items = []
    for entry in feed.entries:
        item = NewsItem(
            id=entry.link,
            source_type="rss",
            source_name=source_name,
            source_url=entry.link,
            created_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            raw_title=entry.title,
            raw_content=entry.summary,
            raw_html=entry.get('content', [{'value': ''}])[0]['value'] if 'content' in entry else "",
            raw_media="",
            raw_tags=",".join(t.term for t in entry.tags) if hasattr(entry, "tags") else ""
        )
        news_items.append(item)
    return news_items
