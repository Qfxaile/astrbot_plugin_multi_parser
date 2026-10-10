from types import SimpleNamespace

from astrbot_multi_parser.services.event_identity import EventIdentity


class Event:
    message_obj = SimpleNamespace(raw_message={"sender": {"card": "群名片"}})

    def get_platform_name(self):
        return "aiocqhttp"

    def get_sender_id(self):
        return 123

    def get_sender_name(self):
        return "公开名称"


def test_event_identity_normalizes_platform_message_and_sender():
    event = Event()

    assert EventIdentity.platform_name(event) == "aiocqhttp"
    assert EventIdentity.sender_identity(event) == ("群名片", "123")
    assert EventIdentity.sender_identity(event, prefer_raw_nickname=True) == (
        "群名片",
        "123",
    )
