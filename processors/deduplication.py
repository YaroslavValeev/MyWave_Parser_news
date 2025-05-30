from hashlib import md5

def add_checksum(news_item):
    checksum_str = md5((news_item.raw_title + news_item.source_url).encode('utf-8')).hexdigest()
    news_item.checksum = checksum_str
    return news_item

def is_duplicate(news_item, existing_checksums):
    return news_item.checksum in existing_checksums
