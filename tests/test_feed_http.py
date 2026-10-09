"""HTTP-загрузка фидов: таймауты и разбор ответа."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.feed_http import fetch_url_bytes, parse_feed_from_url


def test_fetch_url_bytes_ok():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"<feed><entry><title>t</title></entry></feed>"
    with patch("utils.feed_http.requests.get", return_value=mock_resp):
        body, code, err = fetch_url_bytes("https://example.com/feed")
    assert err is None
    assert code == 200
    assert body == mock_resp.content


def test_fetch_url_bytes_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("utils.feed_http.requests.get", return_value=mock_resp):
        body, code, err = fetch_url_bytes("https://example.com/feed")
    assert body is None
    assert code == 503
    assert "503" in (err or "")


def test_parse_feed_from_url_populates_entries():
    xml = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>x</title><id>1</id></entry></feed>"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = xml
    with patch("utils.feed_http.requests.get", return_value=mock_resp):
        feed, diag = parse_feed_from_url("https://example.com/a", source_label="test")
    assert len(feed.entries) == 1
    assert "http_ok" in diag


def test_parse_feed_from_url_bozo():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b"not xml at all {{{"
    with patch("utils.feed_http.requests.get", return_value=mock_resp):
        feed, diag = parse_feed_from_url("https://example.com/bad", source_label="bad")
    assert not feed.entries
    assert "http_ok" in diag or "bozo" in diag
