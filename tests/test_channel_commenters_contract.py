from utils.channel_commenters_contract import (
    build_user_messages_row,
    make_commenter_id,
    validate_user_messages_headers,
)


def test_make_commenter_id_stable():
    a = make_commenter_id(channel_url="https://t.me/foo", message_id="99")
    b = make_commenter_id(channel_url="https://t.me/foo", message_id="99")
    assert a == b
    assert len(a) == 64


def test_build_user_messages_row():
    row = build_user_messages_row(
        {
            "message_id": "501",
            "user_id": "123",
            "user_name": "@alice",
            "post_id": "100",
            "comment_text": "Hello",
            "comment_at": "2026-08-01T12:00:00+00:00",
        }
    )
    assert row["message_type"] == "channel_comment"
    assert row["related_id"] == "100"
    assert row["status"] == "collected"


def test_validate_headers_ok():
    ok, missing = validate_user_messages_headers(
        list(
            "message_id user_id user_name related_id text message_type timestamp status".split()
        )
    )
    assert ok is True
    assert missing == []


def test_validate_headers_missing():
    ok, missing = validate_user_messages_headers(["message_id", "user_id"])
    assert ok is False
    assert "user_name" in missing
