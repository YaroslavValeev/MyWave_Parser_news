from __future__ import annotations

from utils.collect_report import format_collect_report_html, load_collect_report, save_collect_report


def test_save_and_format_collect_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "utils.collect_report.collect_report_path",
        lambda: tmp_path / "last_collect_report.json",
    )
    path = save_collect_report(
        sources_total=2,
        sources_failed=1,
        news_saved=3,
        contacts_saved=0,
        elapsed_seconds=1.5,
        results=[
            {
                "type": "rss",
                "name": "ok-src",
                "url": "https://ok.example/feed",
                "ok": True,
                "news_saved": 3,
                "collected": 5,
                "parsed": 5,
                "duplicates": 2,
                "latency_ms": 100.5,
            },
            {
                "type": "telegram",
                "name": "bad-src",
                "url": "https://t.me/fail",
                "ok": False,
                "error": "FloodWaitError",
            },
        ],
    )
    assert path is not None and path.is_file()
    data = load_collect_report()
    assert data is not None
    assert data["sources_failed"] == 1
    assert data["sources_ok"] == 1
    assert data["results"][0]["duplicates"] == 2
    assert data["results"][0]["latency_ms"] == 100.5
    html = format_collect_report_html(data)
    assert "bad-src" in html
    assert "FloodWaitError" in html
    assert "Telemetry" in html
    assert "ok-src" in html


def test_format_collect_report_html_empty():
    html = format_collect_report_html(None)
    assert "Последний сбор" in html
    assert "Отчёта ещё нет" in html
