from unittest.mock import AsyncMock

import pytest

from services.competitions_from_news import (
    extract_competition_row_from_item,
    extract_competition_rows_from_item,
)
from storage.data import save_news


def test_extract_wakesurf_competition_from_news_item():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Cyprus Wakesurf Championship CWSA 2099",
            "raw_content": (
                "Открыта регистрация\n"
                "📍 Konnos Beach / Айя-Напа\n"
                "📅 6–7 июня 2099\n"
                "Все подробности по ссылке"
            ),
            "link": "https://t.me/prowakesurf/3292",
            "source_url": "https://t.me/prowakesurf",
            "source_name": "prowakesurf",
            "date": "2099-04-23T10:00:00+00:00",
        }
    )

    assert row is not None
    assert row["discipline"] == "wakesurf"
    assert row["event_name"] == "Cyprus Wakesurf Championship CWSA 2099"
    assert row["start_date"] == "2099-06-06"
    assert row["end_date"] == "2099-06-07"
    assert row["source_url"] == "https://t.me/prowakesurf/3292"
    assert row["event_url"] == "https://t.me/prowakesurf/3292"
    assert row["location"] == "Konnos Beach / Айя-Напа"
    assert row["country"] == "Cyprus"
    assert row["status"] == "ACTIVE"
    assert row["id"].startswith("prowakesurf-cyprus-wakesurf-championship-cwsa-2099")


def test_extract_wakeboard_competition_from_news_item():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Wakeboard Open 2099",
            "raw_content": (
                "Registration is open for the wakeboard competition.\n"
                "Location: Orlando Watersports Complex\n"
                "10-12 July 2099"
            ),
            "link": "https://example.com/wakeboard-open",
            "source_url": "https://example.com/feed",
            "source_name": "Wakeboarding Magazine",
            "date": "2099-01-01T10:00:00+00:00",
        }
    )

    assert row is not None
    assert row["discipline"] == "wakeboard"
    assert row["start_date"] == "2099-07-10"
    assert row["end_date"] == "2099-07-12"
    assert row["location"] == "Orlando Watersports Complex"
    assert row["status"] == "ACTIVE"


def test_extract_rss_competition_uses_trusted_source_as_fallback():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Lanier Rider Experience Open 2099",
            "raw_content": (
                "Location: Orlando Watersports Complex\n"
                "10-12 July 2099"
            ),
            "link": "https://example.com/lanier-open",
            "source_url": "https://thewwa.com/feed",
            "source_type": "rss",
            "source_name": "WWA Blog (RSS)",
            "date": "2099-01-01T10:00:00+00:00",
        }
    )

    assert row is not None
    assert row["discipline"] == "wakeboard"
    assert row["event_name"] == "Lanier Rider Experience Open 2099"
    assert row["source_url"] == "https://example.com/lanier-open"


def test_extract_competition_skips_non_target_sup_event():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Альфа-Банк Лопотово",
            "raw_content": (
                "31 мая 2099 пройдет физкультурно-массовое мероприятие по серфингу "
                "в дисциплине доска с веслом."
            ),
            "link": "https://t.me/RFSurf/2769",
            "source_url": "https://t.me/RFSurf",
            "source_name": "RFSurf",
            "date": "2099-04-23T10:00:00+00:00",
        }
    )

    assert row is None


def test_extract_competition_skips_opinion_post():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Своими запретами и бойкотами соревнований",
            "raw_content": "вы показываете детям лицемерие 27 июля 2024",
            "link": "https://t.me/Privat_Wakesurfing/64",
            "source_url": "https://t.me/Privat_Wakesurfing",
            "source_name": "Privat Wakesurfing",
            "date": "2024-07-27T10:00:00+00:00",
        }
    )
    assert row is None


def test_extract_competition_skips_calendar_post_with_cup_snippet():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Календарь спортивных мероприятий на сезон 2099",
            "raw_content": (
                "18-19 июня на базе вейксерф клуба Wakedivision состоится кубок Москвы по вейксерфингу. "
                "Полный календарь сезона смотрите в канале."
            ),
            "link": "https://t.me/surfinmoscow/67",
            "source_url": "https://t.me/surfinmoscow",
            "source_name": "Surf in Moscow",
            "date": "2099-02-01T10:00:00+00:00",
        }
    )
    assert row is None


def test_extract_ruwf_calendar_post_with_multiple_events():
    rows = extract_competition_rows_from_item(
        {
            "raw_title": "Календарь соревнований ФВЛС на сезон 2026",
            "raw_content": (
                "Вейкборд-катер\n\n"
                "Чемпионат России\n"
                "• 13–17 августа 2026 г. – г. Казань, Республика Татарстан\n\n"
                "Первенство России\n"
                "• 10–13 августа 2026 г. – г. Казань, Республика Татарстан\n\n"
                "Межрегиональные соревнования\n"
                "• 31 июля – 2 августа 2026 г. – Свердловская область, г.о. Сысертский\n\n"
                "Всероссийские соревнования\n"
                "• 4–6 сентября 2026 г. – г. Москва, WakeDivision СерБор\n\n"
                "Международные соревнования\n"
                "• Чемпионат мира (вейкборд-катер)\n"
                "• 2–9 августа 2026 г. – Италия"
            ),
            "link": "https://t.me/russian_waterski/120",
            "source_url": "https://t.me/russian_waterski",
            "source_name": "ФВЛС России / RUWF",
            "source_type": "telegram",
            "date": "2026-05-01T10:00:00+00:00",
        }
    )

    assert len(rows) == 5
    names = {row["event_name"] for row in rows}
    assert "Чемпионат России" in names
    assert "Первенство России" in names
    assert "Межрегиональные соревнования" in names
    assert "Всероссийские соревнования" in names
    assert "Чемпионат мира (вейкборд-катер)" in names
    assert all(row["discipline"] == "wakeboard" for row in rows)
    assert all(row["source_url"] == "https://t.me/russian_waterski/120" for row in rows)
    by_name = {row["event_name"]: row for row in rows}
    assert by_name["Чемпионат России"]["start_date"] == "2026-08-13"
    assert by_name["Чемпионат России"]["end_date"] == "2026-08-17"
    assert by_name["Чемпионат России"]["location"] == "г. Казань, Республика Татарстан"
    assert by_name["Чемпионат мира (вейкборд-катер)"]["country"] == "Italy"


def test_extract_competition_skips_generic_calendar_post():
    row = extract_competition_row_from_item(
        {
            "raw_title": "Календарь спортивных мероприятий Российской федерации серфинга на сезон 2099",
            "raw_content": (
                "Сезон начинает набирать обороты, и мы публикуем актуальную информацию "
                "о спортивных соревнованиях, включенных в ЕКП Минспорта России 2099."
            ),
            "link": "https://t.me/surfinmoscow/67",
            "source_url": "https://t.me/surfinmoscow",
            "source_name": "Surf in Moscow",
            "date": "2099-02-01T10:00:00+00:00",
        }
    )

    assert row is None


@pytest.mark.asyncio
async def test_save_news_triggers_auto_competitions_sync(monkeypatch):
    class FakeRepo:
        async def create_item(self, payload):
            return 101

    monkeypatch.setattr("storage.data._ensure_initialized", AsyncMock())
    monkeypatch.setattr("storage.data._REPOSITORY", FakeRepo())
    monkeypatch.setattr("storage.data.sync_ingest_items_batch", AsyncMock(return_value=1))
    sync_mock = AsyncMock(return_value={"input": 1, "upserted": 1})
    monkeypatch.setattr("storage.data.sync_news_competitions", sync_mock)

    saved = await save_news(
        [
            {
                "source": "telegram:prowakesurf",
                "title": "Cyprus Wakesurf Championship CWSA 2099",
                "content": "Открыта регистрация\n📅 6–7 июня 2099",
                "link": "https://t.me/prowakesurf/3292",
                "date": "2099-04-23T10:00:00+00:00",
                "source_name": "prowakesurf",
                "source_url": "https://t.me/prowakesurf",
                "raw_title": "Cyprus Wakesurf Championship CWSA 2099",
                "raw_content": "Открыта регистрация\n📅 6–7 июня 2099",
            }
        ]
    )

    assert saved == 1
    sync_mock.assert_awaited_once()
