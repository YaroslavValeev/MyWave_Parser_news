from collectors.competitions_html_calendar import fetch_calendar_events


def test_fetch_calendar_events_parses_dates(monkeypatch):
    html = """
    <html><body>
    <h3>IWWF Open 2026-09-15 Geneva</h3>
    <li>Wake Festival 2026-10-01 Sochi</li>
    </body></html>
    """

    class FakeResp:
        text = html

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        "collectors.competitions_html_calendar.requests.get",
        lambda *a, **k: FakeResp(),
    )
    rows = fetch_calendar_events("https://example.com", source_name="iwwf")
    assert len(rows) >= 1
    assert rows[0]["status"] == "ACTIVE"
    assert rows[0]["id"].startswith("iwwf-")
