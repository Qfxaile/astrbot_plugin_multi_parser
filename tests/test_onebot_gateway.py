import pytest
from astrbot_multi_parser.services.onebot_gateway import OneBotGateway


class Bot:
    async def call_action(self, action, **params):
        return action, params


class Event:
    bot = Bot()


@pytest.mark.asyncio
async def test_gateway_prefers_call_action():
    assert await OneBotGateway.call(Event(), "test", value=1) == (
        "test",
        {"value": 1},
    )


@pytest.mark.asyncio
async def test_gateway_rejects_missing_client():
    with pytest.raises(RuntimeError, match="没有可用"):
        await OneBotGateway.call(object(), "test")
