from astrbot.api.message_components import Node, Plain
from astrbot_multi_parser.services.delivery.onebot_forward import (
    OneBotForwardSerializer,
)


def test_onebot_forward_serializer_builds_native_text_node():
    import asyncio

    node = Node(content=[Plain("内容")], name="机器人", uin="1")
    messages = asyncio.run(OneBotForwardSerializer.serialize([node], {}))

    assert messages[0]["data"]["nickname"] == "机器人"
    assert messages[0]["data"]["content"][0]["type"] == "text"
