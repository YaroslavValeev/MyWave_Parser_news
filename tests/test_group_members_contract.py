from utils.group_members_contract import (
    MESSAGE_TYPE_GROUP_MEMBER,
    build_user_messages_row,
    make_member_id,
)


def test_make_member_id_stable():
    a = make_member_id(group_id="@club", user_id="42")
    b = make_member_id(group_id="@club", user_id="42")
    c = make_member_id(group_id="@club", user_id="43")
    assert a == b
    assert a != c


def test_build_user_messages_row_member():
    row = build_user_messages_row(
        {
            "member_id": "abc",
            "group_id": "@club",
            "user_id": "10",
            "user_name": "@alice",
            "is_admin": 1,
            "is_bot": 0,
        }
    )
    assert row["message_id"] == "abc"
    assert row["related_id"] == "@club"
    assert row["message_type"] == MESSAGE_TYPE_GROUP_MEMBER
    assert "admin" in row["text"]
