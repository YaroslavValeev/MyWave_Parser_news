from scripts import diagnose_raw_feed_media


class _Response:
    def __init__(self, status_code=200, content_type="image/jpeg"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}


def test_http_image_check_accepts_image_content_type(monkeypatch):
    monkeypatch.setattr(
        diagnose_raw_feed_media.requests,
        "head",
        lambda *args, **kwargs: _Response(content_type="image/webp"),
    )

    ok, error = diagnose_raw_feed_media._http_image_check("https://cdn.example.com/asset", timeout=1)

    assert ok == "yes"
    assert error == ""


def test_http_image_check_rejects_html_page(monkeypatch):
    monkeypatch.setattr(
        diagnose_raw_feed_media.requests,
        "head",
        lambda *args, **kwargs: _Response(content_type="text/html; charset=utf-8"),
    )

    ok, error = diagnose_raw_feed_media._http_image_check("https://example.com/post/123", timeout=1)

    assert ok == "no"
    assert "non_image_content_type:text/html" in error


def test_http_image_check_marks_broken_link(monkeypatch):
    monkeypatch.setattr(
        diagnose_raw_feed_media.requests,
        "head",
        lambda *args, **kwargs: _Response(status_code=404, content_type="text/html"),
    )

    ok, error = diagnose_raw_feed_media._http_image_check("https://cdn.example.com/missing.jpg", timeout=1)

    assert ok == "no"
    assert error == "http_404"
