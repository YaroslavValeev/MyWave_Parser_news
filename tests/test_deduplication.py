from core.processors.deduplication import add_checksum, is_duplicate
from core.models import NewsItem

def test_add_checksum():
    item = NewsItem(
        id="1", source_type="rss", source_name="test", source_url="url",
        created_at="now", raw_title="title", raw_content="content"
    )
    item = add_checksum(item)
    assert item.checksum
    # Checksum must be stable
    item2 = add_checksum(item.copy())
    assert item.checksum == item2.checksum

def test_is_duplicate():
    item = NewsItem(
        id="1", source_type="rss", source_name="test", source_url="url",
        created_at="now", raw_title="title", raw_content="content"
    )
    item = add_checksum(item)
    assert is_duplicate(item, {item.checksum})
    assert not is_duplicate(item, set())
