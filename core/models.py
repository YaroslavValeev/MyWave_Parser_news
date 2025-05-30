from pydantic import BaseModel
from typing import Optional

class NewsItem(BaseModel):
    id: str
    source_type: str
    source_name: str
    source_url: str
    created_at: str
    ingest_status: str = "New"
    raw_title: str
    raw_content: str
    raw_html: Optional[str] = ""
    raw_media: Optional[str] = ""
    lang: Optional[str] = ""
    raw_tags: Optional[str] = ""
    checksum: Optional[str] = ""
    parse_error: Optional[str] = ""
    debug_info: Optional[str] = ""

class SourceItem(BaseModel):
    id: str
    name: str
    url: str
    type: str  # rss, youtube, telegram, website и т.д.
    description: Optional[str] = ""
    lang: Optional[str] = ""
    active: bool = True
    last_id: Optional[str] = None
    filter: Optional[str] = None
