from types import SimpleNamespace

import pytest
from astrbot_multi_parser.services.delivery.video_delivery import VideoDeliveryService


class Event:
    def get_platform_name(self):
        return "telegram"


@pytest.mark.asyncio
async def test_video_delivery_handles_unavailable_platform_name():
    event = SimpleNamespace(
        get_platform_name=lambda: (_ for _ in ()).throw(RuntimeError())
    )
    assert VideoDeliveryService({})._platform_name(event) == ""
