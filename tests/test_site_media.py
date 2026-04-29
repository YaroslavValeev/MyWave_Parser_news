from pathlib import Path

import pytest

from services.site_media import MediaUploadConfig, SiteMediaClient, SiteMediaConfigError


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class DummyHttpClient:
    def __init__(self):
        self.calls = []

    def post(self, url, *, headers, files, data, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "filename": files["file"][0],
                "data": data,
                "timeout": timeout,
            }
        )
        return DummyResponse({"url": "https://site.example/media/test.jpg"})


def test_site_media_client_uses_bearer_token(tmp_path):
    media_file = tmp_path / "test.jpg"
    media_file.write_bytes(b"image")
    http = DummyHttpClient()
    client = SiteMediaClient(
        MediaUploadConfig(url="https://site.example/upload", token="secret"),
        http_client=http,
    )

    result = client.upload_file(media_file, metadata={"raw_id": "1"})

    assert result["url"] == "https://site.example/media/test.jpg"
    assert http.calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert http.calls[0]["filename"] == "test.jpg"
    assert http.calls[0]["data"] == {"raw_id": "1"}


def test_site_media_client_requires_url_and_token(tmp_path):
    media_file = tmp_path / "test.jpg"
    media_file.write_bytes(b"image")
    client = SiteMediaClient(MediaUploadConfig(url="", token=""))

    with pytest.raises(SiteMediaConfigError):
        client.upload_file(media_file)
