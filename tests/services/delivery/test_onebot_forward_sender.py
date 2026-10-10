import pytest
from astrbot_multi_parser.services.delivery.onebot_sender import OneBotForwardSender


class Bot:
    def __init__(self):
        self.calls = []

    async def call_action(self, action, **params):
        self.calls.append((action, params))


class Event:
    def __init__(self, raw):
        self.bot = Bot()
        self.message_obj = type("Message", (), {"raw_message": raw})()

    def get_sender_id(self):
        return 9


@pytest.mark.asyncio
async def test_forward_sender_routes_group_messages():
    event = Event({"group_id": 123})
    await OneBotForwardSender.send(event, [{"type": "node"}])

    assert event.bot.calls[0][0] == "send_group_forward_msg"
    assert event.bot.calls[0][1]["group_id"] == 123
