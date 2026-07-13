from utils.video_providers import build_embed_url, detect_provider, resolve_video_media


def test_youtube_embed():
    video_url, embed_url, provider = build_embed_url("https://youtu.be/dQw4w9WgXcQ")
    assert provider == "youtube"
    assert video_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert embed_url == "https://www.youtube.com/embed/dQw4w9WgXcQ"


def test_vk_embed():
    video_url, embed_url, provider = build_embed_url("https://vk.com/video-123_456")
    assert provider == "vk"
    assert "video_ext.php" in embed_url
    assert "oid=-123" in embed_url
    assert "id=456" in embed_url
    assert video_url.endswith("video-123_456")


def test_rutube_embed():
    _video, embed, provider = build_embed_url("https://rutube.ru/video/abcdef123/")
    assert provider == "rutube"
    assert embed == "https://rutube.ru/play/embed/abcdef123"


def test_vimeo_embed():
    video, embed, provider = build_embed_url("https://vimeo.com/123456789")
    assert provider == "vimeo"
    assert video == "https://vimeo.com/123456789"
    assert embed == "https://player.vimeo.com/video/123456789"


def test_kinescope_embed():
    _video, embed, provider = build_embed_url("https://kinescope.io/abcXYZ")
    assert provider == "kinescope"
    assert "/embed/abcXYZ" in embed


def test_resolve_video_media_external():
    fields = resolve_video_media(
        {
            "videos": "https://www.youtube.com/watch?v=abcdefghijk",
            "cover_image_url": "https://cdn.example.com/poster.jpg",
        },
        poster_url="https://cdn.example.com/poster.jpg",
    )
    assert fields.media_status == "external_video"
    assert fields.embed_url.endswith("/embed/abcdefghijk")
    assert fields.poster_url == "https://cdn.example.com/poster.jpg"


def test_detect_direct_file():
    assert detect_provider("https://cdn.example.com/clip.mp4") == "file"
