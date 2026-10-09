"""RSS media: HTML + og:image fallback for feeds without enclosure."""

from utils.rss_media import extract_images_from_html, extract_images_from_rss_entry


def test_extract_images_from_html_img_and_og():
    html = """
    <html><head>
      <meta property="og:image" content="https://cdn.example.com/cover.jpg">
    </head><body>
      <img src="/logo.png" />
      <img src="https://cdn.example.com/photo.webp" />
    </body></html>
    """
    images = extract_images_from_html(html, base_url="https://example.com/post/")
    assert images[0] == "https://cdn.example.com/cover.jpg"
    assert "https://cdn.example.com/photo.webp" in images


def test_extract_images_skips_wordpress_emoji():
    html = '<img src="https://s.w.org/images/core/emoji/17.0.2/72x72/2122.png">'
    assert extract_images_from_html(html) == []


def test_collect_prefers_og_image(monkeypatch):
    from utils import rss_media as mod

    entry = {
        "link": "https://example.com/a",
        "summary": '<img src="https://cdn.example.com/specs.jpg">',
    }
    monkeypatch.setattr(
        mod,
        "fetch_og_image_from_article",
        lambda url, timeout=15.0: "https://cdn.example.com/hero.jpg",
    )
    images = mod.collect_rss_entry_images(entry, fetch_article_og=True)
    assert images[0] == "https://cdn.example.com/hero.jpg"
    assert "https://cdn.example.com/specs.jpg" in images


def test_extract_images_from_rss_entry_media_content():
    entry = {
        "link": "https://example.com/a",
        "media_content": [{"url": "https://cdn.example.com/a.jpg", "type": "image/jpeg"}],
        "summary": "<p>no img</p>",
    }
    assert extract_images_from_rss_entry(entry) == ["https://cdn.example.com/a.jpg"]


def test_extract_images_from_rss_entry_summary_img():
    entry = {
        "link": "https://example.com/a",
        "summary": '<p><img src="/media/hero.jpg"></p>',
    }
    assert extract_images_from_rss_entry(entry) == ["https://example.com/media/hero.jpg"]
