import pytest

from utils.safe_http import UnsafeURLError, assert_public_http_url


def test_assert_public_http_url_allows_https():
    assert assert_public_http_url("https://cdn.example.com/a.jpg").startswith("https://")


def test_assert_public_http_url_blocks_localhost():
    with pytest.raises(UnsafeURLError):
        assert_public_http_url("http://127.0.0.1/secret")


def test_assert_public_http_url_blocks_metadata_ip():
    with pytest.raises(UnsafeURLError):
        assert_public_http_url("http://169.254.169.254/latest/meta-data")


def test_assert_public_http_url_rejects_empty():
    with pytest.raises(UnsafeURLError):
        assert_public_http_url("")
