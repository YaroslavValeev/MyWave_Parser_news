from utils.group_authors_contract import (
    build_user_messages_row,
    make_author_id,
    normalize_group_target,
    parse_group_ids,
    parse_group_targets,
    to_telethon_peer_id,
)


def test_parse_group_ids_owner_paste():
    raw = (
        "3099511486, id: 1 701 957 243, id: 1 606 459 352, id: 1 791 775 467, "
        "id: 2 441 251 847, id: 1 738 625 669, id: 2 147 018 906, id: 1 124 621 462"
    )
    ids = parse_group_ids(raw)
    assert ids == [
        "3099511486",
        "1701957243",
        "1606459352",
        "1791775467",
        "2441251847",
        "1738625669",
        "2147018906",
        "1124621462",
    ]


def test_parse_moscow_usernames():
    raw = (
        "@sportstation_club,@WAKEPARKSTROGINO,https://t.me/wakedivision,"
        "1609224876,1124621462"
    )
    targets = parse_group_targets(raw)
    assert "@sportstation_club" in targets
    assert "@WAKEPARKSTROGINO" in targets
    assert "@wakedivision" in targets
    assert "1609224876" in targets
    assert "1124621462" in targets


def test_normalize_group_target():
    assert normalize_group_target("@wakedivision") == "@wakedivision"
    assert normalize_group_target("https://t.me/sportstation_club") == "@sportstation_club"
    assert normalize_group_target("1609224876") == "1609224876"
    assert normalize_group_target("-1001124621462") == "-1001124621462"


def test_make_author_id_stable_and_dedup_key():
    a = make_author_id(group_id="@wakedivision", user_id="42")
    b = make_author_id(group_id="@wakedivision", user_id="42")
    c = make_author_id(group_id="@wakedivision", user_id="43")
    assert a == b
    assert a != c
    assert len(a) == 64


def test_to_telethon_peer_id():
    assert to_telethon_peer_id("1701957243") == -1001701957243
    assert to_telethon_peer_id(-1001701957243) == -1001701957243


def test_build_user_messages_row_group_author():
    row = build_user_messages_row(
        {
            "author_id": "abc123",
            "user_id": "99",
            "user_name": "@wake",
            "group_id": "@wakedivision",
            "message_snippet": "hello wakesurf",
            "last_message_at": "2026-11-06T00:00:00+00:00",
        }
    )
    assert row["message_id"] == "abc123"
    assert row["message_type"] == "group_message_author"
    assert row["related_id"] == "@wakedivision"
    assert row["status"] == "collected"
