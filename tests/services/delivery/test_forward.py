from astrbot.api.message_components import Image, Node, Plain
from astrbot_multi_parser.services.delivery.forward import ForwardDeliveryService
from astrbot_multi_parser.services.delivery.onebot_forward import (
    OneBotForwardSerializer,
)
from result_factory import build_result


class Event:
    def __init__(self, private: bool):
        self.private = private

    def is_private_chat(self):
        return self.private

    def chain_result(self, chain):
        return chain

    def get_platform_name(self):
        return "unsupported-platform"


def test_onebot_forward_serializer_builds_native_text_node():
    import asyncio

    node = Node(content=[Plain("内容")], name="机器人", uin="1")
    messages = asyncio.run(OneBotForwardSerializer.serialize([node], {}))

    assert messages[0]["data"]["nickname"] == "机器人"
    assert messages[0]["data"]["content"][0]["type"] == "text"


def test_non_forward_platform_limits_group_messages_but_not_private_messages():
    components = [
        Plain("第一段"),
        Image(file="image-1"),
        Plain("第二段"),
        Image(file="image-2"),
    ]
    service = ForwardDeliveryService({"non_forward_message_limit": 2})

    group_results = service._build_non_forward(Event(private=False), components)
    private_results = service._build_non_forward(Event(private=True), components)

    assert len(group_results) == 3
    assert "共 4 条" in group_results[0][0].text
    assert "上限 2 条" in group_results[0][0].text
    assert group_results[1:] == [[components[0]], [components[1]]]
    assert private_results == [components]


def test_non_forward_private_limit_can_be_enabled():
    components = [Plain("第一段"), Image(file="image-1"), Plain("第二段")]
    service = ForwardDeliveryService(
        {"non_forward_message_limit": 2, "limit_private_chat_messages": True}
    )

    limited = service._build_non_forward(Event(private=True), components)

    assert "共 3 条" in limited[0][0].text
    assert limited[1:] == [[components[0]], [components[1]]]


def test_non_forward_limit_supports_unlimited_and_blocked_modes():
    components = [Plain("第一段"), Image(file="image-1"), Plain("第二段")]
    unlimited = ForwardDeliveryService({"non_forward_message_limit": -1})
    blocked = ForwardDeliveryService({"non_forward_message_limit": 0})

    assert unlimited._build_non_forward(Event(private=False), components) == [
        components
    ]
    blocked_results = blocked._build_non_forward(Event(private=False), components)
    assert len(blocked_results) == 1
    assert "共 3 条" in blocked_results[0][0].text
    assert "前 0 条" in blocked_results[0][0].text


def test_build_limits_rendered_non_forward_content_for_group_chat():
    service = ForwardDeliveryService({"non_forward_message_limit": 2})
    result = build_result(
        "unsupported-platform",
        title="标题",
        image_urls=["image-1", "image-2", "image-3"],
    )

    group_results, _ = service.build(
        Event(private=False),
        result,
        [Plain("标题"), Image(file="image-1"), Plain("正文"), Image(file="image-2")],
        include_video=False,
    )
    private_results, _ = service.build(
        Event(private=True),
        result,
        [Plain("标题"), Image(file="image-1"), Plain("正文"), Image(file="image-2")],
        include_video=False,
    )

    assert len(group_results) == 3
    assert "共 4 条" in group_results[0][0].text
    assert group_results[1] == [Plain("标题")]
    assert isinstance(group_results[2][0], Image)
    assert len(private_results) == 1
