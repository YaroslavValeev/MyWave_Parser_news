"""Нормализация строк сборщика в payload для репозитория (без сети)."""
from __future__ import annotations

import pytest

from dataclasses import dataclass

from services.collector_runner import (
    _compute_checksum,
    _normalize_to_item,
    _safe_json,
    _split_db_source,
)


def test_split_db_source_type_name():
    assert _split_db_source("rss:Wake Mag") == ("rss", "Wake Mag")
    assert _split_db_source("solo") == ("", "solo")


def test_normalize_to_item_dict():
    row = {
        "raw_title": "  T  ",
        "raw_content": "body",
        "link": "https://example.com/p/1",
        "checksum": "fixed",
    }
    out = _normalize_to_item(row, "rss", "Site", "https://feed")
    assert out is not None
    assert out["raw_title"] == "T"
    assert out["raw_content"] == "body"
    assert out["checksum"] == "fixed"
    assert out["status"] == "new"


def test_normalize_to_item_yields_source_url_from_link():
    row = {"title": "Only title", "content": "c", "link": "https://watch?v=abc"}
    out = _normalize_to_item(row, "youtube", "Ch", "https://youtube.com/channel/x")
    assert out is not None
    assert "watch?v=abc" in (out.get("source_url") or "")


def test_safe_json():
    assert _safe_json(None) == ""
    assert _safe_json("plain") == "plain"
    assert '"a"' in _safe_json({"a": 1}) or _safe_json({"a": 1}) == '{"a": 1}'


def test_normalize_to_item_unknown_shape_returns_none():
    assert _normalize_to_item(object(), "rss", "N", "https://x") is None


def test_normalize_debug_info_yt_link():
    row = {"title": "V", "content": "", "debug_info": "noise yt_link=https://youtu.be/abc123"}
    out = _normalize_to_item(row, "youtube", "Ch", "")
    assert out is not None
    assert out.get("source_url") == "https://youtu.be/abc123"


@dataclass
class _Dc:
    title: str
    content: str
    link: str


def test_normalize_dataclass_row():
    out = _normalize_to_item(
        _Dc(title="Hello", content="world", link="https://post"),
        "website",
        "Blog",
        "https://home",
    )
    assert out is not None
    assert out["raw_title"] == "Hello"
    assert out["source_url"] == "https://post"


def test_compute_checksum_legacy_wrapper():
    h = _compute_checksum("same", "")
    assert len(h) == 32


class _DummyModel:
    def model_dump(self):
        return {
            "title": "M",
            "content": "",
            "link": "",
            "checksum": "ck",
            "raw_html": "",
        }


def test_normalize_pydantic_like_model_dump():
    out = _normalize_to_item(_DummyModel(), "rss", "R", "https://feed")
    assert out is not None
    assert out["raw_title"] == "M"
    assert out["checksum"] == "ck"
