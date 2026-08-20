import base64
from types import SimpleNamespace

import httpx
import pytest
from astrbot.api.message_components import Image, Node, Nodes, Plain, Record, Video
from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
from astrbot.core.star.star_handler import star_handlers_registry
from astrbot_multi_parser import main
from astrbot_multi_parser.core import media
from astrbot_multi_parser.core.contracts import OrderedContent, ParseResult
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.main import MultiParserPlugin, VideoSizeInfo
from astrbot_multi_parser.services.delivery import DeliveryService

TEST_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeParser:
    name = "fake"

    def __init__(self, result: ParseResult):
        self.result = result

    async def match(self, context):
        return True

    async def parse(self, context):
        return self.result


class FailingParser:
    name = "fake"

    async def match(self, context):
        return True

    async def parse(self, context):
        raise CookieAccessError("测试平台", configured=False)


class NonMatchingParser:
    name = "non-matching"

    async def match(self, context):
        return False


class MatchFailingParser:
    name = "match-failing"

    async def match(self, context):
        raise RuntimeError("match failed")


class FakeBot:
    def __init__(self, failure=None, responses=None):
        self.failure = failure
        self.responses = responses or {}
        self.actions = []

    async def call_action(self, action, **params):
        self.actions.append((action, params))
        if self.failure is not None:
            raise self.failure
        return self.responses.get(action)


class FakeEvent:
    def __init__(
        self,
        sender_id=123,
        sender_name="",
        sender=None,
        raw_message=None,
        platform_name="aiocqhttp",
        platform_id="测试机器人",
        forward_failure_limit=None,
        bot=None,
        has_send_oper=False,
    ):
        self.sender_id = sender_id
        self.sender_name = sender_name
        self.platform_name = platform_name
        self.platform_id = platform_id
        self.forward_failure_limit = forward_failure_limit
        self.bot = bot
        self._has_send_oper = has_send_oper
        self.sent = []
        self.forward_attempt_sizes = []
        self.message_obj = SimpleNamespace(
            raw_message=(
                {"sender": sender or {}} if raw_message is None else raw_message
            ),
            message_id="",
        )
        self.unified_msg_origin = "test:private:admin"
        self.private = True

    def get_sender_id(self):
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_platform_name(self):
        if self.platform_name == "__raise__":
            raise RuntimeError("platform unavailable")
        return self.platform_name

    def get_platform_id(self):
        return self.platform_id

    def get_self_id(self):
        raw = self.message_obj.raw_message
        return str(raw.get("self_id") or "") if isinstance(raw, dict) else ""

    def get_group_id(self):
        raw = self.message_obj.raw_message
        return str(raw.get("group_id") or "") if isinstance(raw, dict) else ""

    def is_private_chat(self):
        return self.private

    def chain_result(self, chain):
        return chain

    def plain_result(self, text):
        return [Plain(text)]

    async def send(self, message):
        self._has_send_oper = True
        chain = list(message.chain)
        if len(chain) == 1 and isinstance(chain[0], Nodes):
            node_count = len(chain[0].nodes)
            self.forward_attempt_sizes.append(node_count)
            if (
                self.forward_failure_limit is not None
                and node_count > self.forward_failure_limit
            ):
                raise RuntimeError("forward rejected")
        self.sent.append(chain)


class FakeConversationManager:
    def __init__(self, current_conversation_id="conversation-id", failure=None):
        self.current_conversation_id = current_conversation_id
        self.failure = failure
        self.created = []
        self.add_calls = 0
        self.message_pairs = []

    async def get_curr_conversation_id(self, unified_msg_origin):
        return self.current_conversation_id

    async def new_conversation(self, unified_msg_origin, platform_id=None):
        self.created.append((unified_msg_origin, platform_id))
        self.current_conversation_id = "new-conversation-id"
        return self.current_conversation_id

    async def add_message_pair(self, conversation_id, user_message, assistant_message):
        self.add_calls += 1
        if self.failure is not None:
            raise self.failure
        self.message_pairs.append((conversation_id, user_message, assistant_message))


def make_plugin(result: ParseResult, *, conversation_manager=None, **config):
    plugin = MultiParserPlugin.__new__(MultiParserPlugin)
    plugin.config = {
        "platform_switches": {"fake": True},
        "enable_parse_reaction": False,
        "send_video_by_url": True,
        **config,
    }
    plugin.parsers = {"fake": FakeParser(result)}
    plugin.context = SimpleNamespace(
        conversation_manager=conversation_manager or FakeConversationManager()
    )
    return plugin


def test_plugin_registers_all_supported_parsers():
    plugin = MultiParserPlugin(None, {})

    assert set(plugin.parsers) == {
        "bilibili",
        "douyin",
        "fanqie",
        "redbook",
        "tieba",
        "weibo",
        "wechat",
        "xiaoheihe",
        "zhihu",
        "github",
        "qzone",
        "pixiv",
    }


def test_plugin_respects_platform_switches():
    config = {
        "platform_switches": {
            "bilibili": False,
            "douyin": True,
            "fanqie": False,
            "redbook": False,
            "tieba": False,
            "weibo": False,
            "wechat": False,
            "xiaoheihe": True,
            "zhihu": False,
            "github": False,
            "qzone": False,
            "pixiv": False,
        }
    }

    plugin = MultiParserPlugin(None, config)

    assert plugin._enabled_parsers() == [
        plugin.parsers["douyin"],
        plugin.parsers["xiaoheihe"],
    ]


@pytest.mark.parametrize(
    "handler_name",
    [
        "platform_login",
        "platform_login_status",
        "platform_logout",
        "cancel_platform_login",
    ],
)
def test_authentication_commands_require_global_admin(handler_name):
    handler = getattr(MultiParserPlugin, handler_name)
    metadata = next(item for item in star_handlers_registry if item.handler is handler)

    assert any(
        isinstance(event_filter, PermissionTypeFilter)
        and event_filter.permission_type == PermissionType.ADMIN
        for event_filter in metadata.event_filters
    )


async def collect_results(monkeypatch, result, event=None, **config):
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )
    plugin = make_plugin(result, **config)
    target_event = event or FakeEvent()
    return await collect_plugin_results(plugin, target_event)


async def collect_plugin_results(plugin, event):
    messages = []
    sent_count = 0
    async for item in plugin.handle_parse(event):
        messages.extend(event.sent[sent_count:])
        sent_count = len(event.sent)
        messages.append(item)
    messages.extend(event.sent[sent_count:])
    return messages


@pytest.mark.asyncio
async def test_parse_delivery_restores_unsent_event_state(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    plugin = make_plugin(ParseResult(platform="测试平台", title="解析结果"))
    event = FakeEvent(has_send_oper=False)

    async for _ in plugin.handle_parse(event):
        # AstrBot 的响应阶段发送 yield 结果后会标记本次事件已经发送消息。
        event._has_send_oper = True

    assert event._has_send_oper is False


@pytest.mark.asyncio
async def test_parse_delivery_preserves_existing_sent_event_state(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    plugin = make_plugin(ParseResult(platform="测试平台", title="解析结果"))
    event = FakeEvent(has_send_oper=True)

    async for _ in plugin.handle_parse(event):
        event._has_send_oper = True

    assert event._has_send_oper is True


@pytest.mark.asyncio
async def test_parse_failure_restores_unsent_event_state(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    plugin = make_plugin(ParseResult(platform="fake"))
    plugin.parsers = {"fake": FailingParser()}
    event = FakeEvent(has_send_oper=False)

    async for _ in plugin.handle_parse(event):
        event._has_send_oper = True

    assert event._has_send_oper is False


@pytest.mark.asyncio
async def test_match_failure_restores_unsent_event_state(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    plugin = make_plugin(ParseResult(platform="fake"))
    plugin.parsers = {"fake": MatchFailingParser()}
    event = FakeEvent(has_send_oper=False)

    async for _ in plugin.handle_parse(event):
        event._has_send_oper = True

    assert event._has_send_oper is False


@pytest.mark.asyncio
@pytest.mark.parametrize("has_send_oper", [False, True])
async def test_unmatched_parse_keeps_event_state(monkeypatch, has_send_oper):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="ordinary message"),
    )
    plugin = make_plugin(ParseResult(platform="fake"))
    plugin.parsers = {"fake": NonMatchingParser()}
    event = FakeEvent(has_send_oper=has_send_oper)

    assert [item async for item in plugin.handle_parse(event)] == []
    assert event._has_send_oper is has_send_oper


@pytest.mark.asyncio
async def test_conversation_history_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    conversation_manager = FakeConversationManager()
    plugin = make_plugin(
        ParseResult(platform="测试平台", title="测试标题"),
        conversation_manager=conversation_manager,
    )

    await collect_plugin_results(plugin, FakeEvent())

    assert conversation_manager.created == []
    assert conversation_manager.message_pairs == []


@pytest.mark.asyncio
async def test_conversation_history_defaults_to_text_only(monkeypatch, tmp_path):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(TEST_PNG)
    conversation_manager = FakeConversationManager()
    plugin = make_plugin(
        ParseResult(
            platform="测试平台",
            ordered_contents=[
                OrderedContent("text", "正文内容"),
                OrderedContent("image", str(image_path)),
            ],
            temporary_files=[image_path],
        ),
        conversation_manager=conversation_manager,
        enable_conversation_history=True,
    )

    await collect_plugin_results(plugin, FakeEvent())

    content = conversation_manager.message_pairs[0][2]["content"]
    assert isinstance(content, str)
    assert "正文内容" in content
    assert "图片: 1 张" in content
    assert "data:image/" not in content
    assert str(image_path) not in content


@pytest.mark.asyncio
async def test_successful_parse_is_added_to_current_conversation(monkeypatch, tmp_path):
    parse_context = SimpleNamespace(combined_text="帮我看看 https://example.com/post")
    monkeypatch.setattr(main, "extract_context", lambda event: parse_context)
    conversation_manager = FakeConversationManager()
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(TEST_PNG)
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        author="测试作者",
        description="测试简介",
        video_url="https://video.example/play.mp4?token=secret",
        audio_url="https://audio.example/play.m4a?token=secret",
        extra_lines=["附加信息"],
        ordered_contents=[
            OrderedContent("text", "正文内容"),
            OrderedContent("image", str(image_path)),
        ],
        temporary_files=[image_path],
    )
    plugin = make_plugin(
        result,
        conversation_manager=conversation_manager,
        send_video_by_url=False,
        enable_conversation_history=True,
        conversation_history_mode="text_and_images",
    )

    await collect_plugin_results(plugin, FakeEvent())

    assert len(conversation_manager.message_pairs) == 1
    conversation_id, user_message, assistant_message = (
        conversation_manager.message_pairs[0]
    )
    assert conversation_id == "conversation-id"
    assert user_message == {
        "role": "user",
        "content": parse_context.combined_text,
    }
    assistant_content = assistant_message["content"]
    assert assistant_message["role"] == "assistant"
    assert [part["type"] for part in assistant_content] == [
        "text",
        "text",
        "image_url",
        "text",
    ]
    summary = assistant_content[0]["text"]
    assert "[由多平台内容解析插件发送]" in summary
    assert "平台: 测试平台" in summary
    assert "标题: 测试标题" in summary
    assert "作者: 测试作者" in summary
    assert "测试简介" in summary
    assert "附加信息" in summary
    assert assistant_content[1] == {"type": "text", "text": "正文内容"}
    assert assistant_content[2]["image_url"]["url"].startswith("data:image/png;base64,")
    assert (
        base64.b64decode(assistant_content[2]["image_url"]["url"].split(",", 1)[1])
        == TEST_PNG
    )
    assert "视频: 已发送" in assistant_content[3]["text"]
    assert "音频: 已发送" in assistant_content[3]["text"]
    assert "token=secret" not in str(assistant_content)
    assert not image_path.exists()


@pytest.mark.asyncio
async def test_conversation_history_preserves_ordered_text_and_images(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    first_image = tmp_path / "first.png"
    second_image = tmp_path / "second.png"
    first_image.write_bytes(TEST_PNG)
    second_image.write_bytes(TEST_PNG)
    conversation_manager = FakeConversationManager()
    plugin = make_plugin(
        ParseResult(
            platform="测试平台",
            ordered_contents=[
                OrderedContent("text", "第一段"),
                OrderedContent("image", str(first_image)),
                OrderedContent("text", "第二段"),
                OrderedContent("image", str(second_image)),
            ],
            temporary_files=[first_image, second_image],
        ),
        conversation_manager=conversation_manager,
        enable_conversation_history=True,
        conversation_history_mode="text_and_images",
    )

    await collect_plugin_results(plugin, FakeEvent())

    content = conversation_manager.message_pairs[0][2]["content"]
    assert [part["type"] for part in content] == [
        "text",
        "text",
        "image_url",
        "text",
        "image_url",
    ]
    assert content[1] == {"type": "text", "text": "第一段"}
    assert content[3] == {"type": "text", "text": "第二段"}


@pytest.mark.asyncio
async def test_successful_parse_creates_conversation_when_missing(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    conversation_manager = FakeConversationManager(current_conversation_id=None)
    plugin = make_plugin(
        ParseResult(platform="测试平台", title="测试标题"),
        conversation_manager=conversation_manager,
        enable_conversation_history=True,
    )
    event = FakeEvent(platform_id="platform-instance")

    await collect_plugin_results(plugin, event)

    assert conversation_manager.created == [
        (event.unified_msg_origin, "platform-instance")
    ]
    assert conversation_manager.message_pairs[0][0] == "new-conversation-id"


@pytest.mark.asyncio
async def test_conversation_write_failure_does_not_break_parse_delivery(monkeypatch):
    monkeypatch.setattr(
        main,
        "extract_context",
        lambda event: SimpleNamespace(combined_text="https://example.com/post"),
    )
    conversation_manager = FakeConversationManager(
        failure=RuntimeError("database unavailable")
    )
    plugin = make_plugin(
        ParseResult(platform="测试平台", title="仍应发送"),
        conversation_manager=conversation_manager,
        forward_mode="never",
        enable_conversation_history=True,
    )

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert messages[0][0].text == "仍应发送"
    assert conversation_manager.add_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_name", ["知乎", "微信", "小黑盒"])
async def test_platform_login_rejects_group_chat_before_starting_login(platform_name):
    plugin = make_plugin(ParseResult(platform="fake"))
    authentication = SimpleNamespace(login=None)
    plugin._authentication = authentication
    event = FakeEvent()
    event.private = False

    messages = [item async for item in plugin.platform_login(event, platform_name)]

    assert messages[0][0].text == "平台登录仅允许管理员在私聊中操作。"
    assert authentication.login is None


@pytest.mark.asyncio
async def test_platform_login_status_allows_admin_group_query():
    class FakeAuthentication:
        def __init__(self):
            self.calls = 0

        async def status(self):
            self.calls += 1
            return "平台登录状态：\n- B站：已配置｜当前用户：测试用户（UID：12345）"

    plugin = make_plugin(ParseResult(platform="fake"))
    authentication = FakeAuthentication()
    plugin._authentication = authentication
    event = FakeEvent()
    event.private = False

    messages = [item async for item in plugin.platform_login_status(event)]

    assert messages[0][0].text.endswith("测试用户（UID：12345）")
    assert authentication.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_name", ["小红书", "知乎", "微信"])
async def test_platform_login_delegates_chinese_platform_name_in_private_chat(
    platform_name,
):
    class FakeAuthentication:
        def __init__(self):
            self.calls = []

        async def login(self, event, platform_name):
            self.calls.append((event, platform_name))
            return f"{platform_name}登录成功，Cookies 已保存。"

    plugin = make_plugin(ParseResult(platform="fake"))
    authentication = FakeAuthentication()
    plugin._authentication = authentication
    event = FakeEvent()

    messages = [item async for item in plugin.platform_login(event, platform_name)]

    assert messages[0][0].text == f"{platform_name}登录成功，Cookies 已保存。"
    assert authentication.calls == [(event, platform_name)]


@pytest.mark.asyncio
async def test_platform_login_delegates_tieba_name_in_private_chat():
    class FakeAuthentication:
        def __init__(self):
            self.calls = []

        async def login(self, event, platform_name):
            self.calls.append((event, platform_name))
            return "贴吧登录成功，Cookies 已保存。"

    plugin = make_plugin(ParseResult(platform="fake"))
    authentication = FakeAuthentication()
    plugin._authentication = authentication
    event = FakeEvent()

    messages = [item async for item in plugin.platform_login(event, "贴吧")]

    assert messages[0][0].text == "贴吧登录成功，Cookies 已保存。"
    assert authentication.calls == [(event, "贴吧")]


@pytest.mark.asyncio
async def test_platform_login_delegates_weibo_chinese_platform_name():
    class FakeAuthentication:
        def __init__(self):
            self.calls = []

        async def login(self, event, platform_name):
            self.calls.append((event, platform_name))
            return "微博登录成功，Cookies 已保存。"

    plugin = make_plugin(ParseResult(platform="fake"))
    authentication = FakeAuthentication()
    plugin._authentication = authentication
    event = FakeEvent()

    messages = [item async for item in plugin.platform_login(event, "微博")]

    assert messages[0][0].text == "微博登录成功，Cookies 已保存。"
    assert authentication.calls == [(event, "微博")]


@pytest.mark.asyncio
async def test_platform_login_delegates_xiaoheihe_chinese_platform_name():
    class FakeAuthentication:
        def __init__(self):
            self.calls = []

        async def login(self, event, platform_name):
            self.calls.append((event, platform_name))
            return "小黑盒登录成功，Cookies 已保存。"

    plugin = make_plugin(ParseResult(platform="fake"))
    authentication = FakeAuthentication()
    plugin._authentication = authentication
    event = FakeEvent()

    messages = [item async for item in plugin.platform_login(event, "小黑盒")]

    assert messages[0][0].text == "小黑盒登录成功，Cookies 已保存。"
    assert authentication.calls == [(event, "小黑盒")]


@pytest.mark.asyncio
async def test_handle_parse_outputs_cookie_failure_without_generic_prefix(monkeypatch):
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )
    plugin = make_plugin(ParseResult(platform="fake"))
    plugin.parsers = {"fake": FailingParser()}

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert messages[0][0].text == (
        "测试平台内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"
    )
    assert "fake 解析失败" not in messages[0][0].text


@pytest.mark.asyncio
async def test_handle_parse_cleans_temporary_images_after_send(monkeypatch, tmp_path):
    image_path = tmp_path / "original.webp"
    image_path.write_bytes(b"original-image")
    result = ParseResult(
        platform="test",
        image_urls=[str(image_path)],
        temporary_files=[image_path],
    )

    messages = await collect_results(monkeypatch, result)

    assert messages[0][0].file == image_path.resolve().as_uri()
    assert messages[0][0].path == str(image_path.resolve())
    assert not image_path.exists()
    assert result.temporary_files == []


@pytest.mark.asyncio
async def test_two_images_keep_legacy_info_chain_order(monkeypatch):
    result = ParseResult(
        platform="test",
        title="标题",
        cover_urls=["base64://cover"],
        image_urls=["base64://image"],
    )

    messages = await collect_results(monkeypatch, result)

    assert len(messages) == 1
    assert [type(component) for component in messages[0]] == [Image, Image, Plain]
    assert [component.file for component in messages[0][:2]] == [
        "base64://cover",
        "base64://image",
    ]


@pytest.mark.asyncio
async def test_exactly_three_images_are_sent_as_one_forward_message(
    monkeypatch,
):
    result = ParseResult(
        platform="test",
        title="标题",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )

    messages = await collect_results(monkeypatch, result)

    assert len(messages) == 1
    assert len(messages[0]) == 1
    assert isinstance(messages[0][0], Nodes)
    nodes = messages[0][0].nodes
    assert len(nodes) == 4
    assert all(isinstance(node, Node) and len(node.content) == 1 for node in nodes)
    assert all(isinstance(node.content[0], Image) for node in nodes[:3])
    assert isinstance(nodes[3].content[0], Plain)
    assert nodes[3].content[0].text == "标题"


@pytest.mark.asyncio
async def test_four_images_create_four_nodes(monkeypatch):
    result = ParseResult(
        platform="test",
        image_urls=[f"base64://{index}" for index in range(4)],
    )

    messages = await collect_results(monkeypatch, result)

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)
    assert [node.content[0].file for node in messages[0][0].nodes] == [
        "base64://0",
        "base64://1",
        "base64://2",
        "base64://3",
    ]


@pytest.mark.asyncio
async def test_adjacent_forward_text_is_merged_with_newlines(monkeypatch):
    contents = [
        OrderedContent("text", "第一段\n"),
        OrderedContent("text", "\n第二段"),
        OrderedContent("image", "base64://image"),
        OrderedContent("text", "第三段"),
        OrderedContent("text", "第四段"),
    ]

    messages = await collect_results(
        monkeypatch,
        ParseResult(platform="test", ordered_contents=contents),
        forward_mode="always",
    )

    nodes = messages[0][0].nodes
    assert [type(node.content[0]) for node in nodes] == [Plain, Image, Plain]
    assert nodes[0].content[0].text == "第一段\n第二段"
    assert nodes[1].content[0].file == "base64://image"
    assert nodes[2].content[0].text == "第三段\n第四段"


@pytest.mark.asyncio
async def test_forward_is_split_at_official_node_limit(monkeypatch):
    result = ParseResult(
        platform="test",
        image_urls=[f"base64://{index}" for index in range(101)],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="always",
    )

    assert [len(message[0].nodes) for message in messages] == [51, 50]
    assert [
        node.content[0].file for message in messages for node in message[0].nodes
    ] == [f"base64://{index}" for index in range(101)]


@pytest.mark.asyncio
async def test_rejected_forward_batch_is_not_split_or_retried(
    monkeypatch,
):
    event = FakeEvent(forward_failure_limit=6)
    result = ParseResult(
        platform="test",
        image_urls=[f"base64://{index}" for index in range(7)],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        event=event,
        forward_mode="always",
    )

    assert event.forward_attempt_sizes == [7]
    assert len(messages) == 1
    assert isinstance(messages[0][0], Plain)
    assert messages[0][0].text == "fake 合并转发发送失败: forward rejected"


@pytest.mark.asyncio
async def test_rejected_single_forward_node_is_not_retried(
    monkeypatch,
):
    event = FakeEvent(forward_failure_limit=0)

    messages = await collect_results(
        monkeypatch,
        ParseResult(platform="test", title="正文"),
        event=event,
        forward_mode="always",
    )

    assert event.forward_attempt_sizes == [1]
    assert len(messages) == 1
    assert isinstance(messages[0][0], Plain)
    assert messages[0][0].text == "fake 合并转发发送失败: forward rejected"


@pytest.mark.asyncio
async def test_aiocqhttp_forward_uses_remote_image_url_without_base64(
    monkeypatch,
    tmp_path,
):
    image_paths = [tmp_path / f"original-{index}.jpg" for index in range(7)]
    source_urls = [f"https://img.example/original-{index}.jpg" for index in range(7)]
    for image_path in image_paths:
        image_path.write_bytes(b"large-original-image")
    result = ParseResult(
        platform="test",
        image_urls=[str(image_path) for image_path in image_paths],
        temporary_files=image_paths,
        image_source_urls={
            str(image_path.resolve()): source_url
            for image_path, source_url in zip(image_paths, source_urls, strict=True)
        },
    )
    bot = FakeBot()
    event = FakeEvent(
        bot=bot,
        raw_message={
            "group_id": 10001,
            "self_id": 20002,
            "sender": {"nickname": "测试用户"},
        },
    )

    messages = await collect_results(
        monkeypatch,
        result,
        event=event,
        forward_mode="always",
    )

    assert messages == []
    assert bot.actions[0] == ("get_login_info", {"self_id": 20002})
    assert len(bot.actions) == 2
    action, params = bot.actions[1]
    assert action == "send_group_forward_msg"
    assert params["group_id"] == 10001
    assert params["self_id"] == 20002
    assert len(params["messages"]) == 7
    assert [
        node["data"]["content"][0]["data"]["file"] for node in params["messages"]
    ] == source_urls
    assert not any(
        node["data"]["content"][0]["data"]["file"].startswith("base64://")
        for node in params["messages"]
    )
    assert not any(image_path.exists() for image_path in image_paths)


@pytest.mark.asyncio
async def test_pixiv_forward_asks_napcat_to_download_images_with_headers(
    monkeypatch, tmp_path
):
    image_paths = [tmp_path / f"pixiv-{index}.jpg" for index in range(2)]
    source_urls = [
        f"https://i.pximg.net/img-original/pixiv-{index}.jpg" for index in range(2)
    ]
    for image_path in image_paths:
        image_path.write_bytes(b"pixiv-image")
    result = ParseResult(
        platform="pixiv",
        title="Pixiv作品",
        image_urls=[str(image_path) for image_path in image_paths],
        temporary_files=image_paths,
        image_source_urls={
            str(image_path.resolve()): source_url
            for image_path, source_url in zip(image_paths, source_urls, strict=True)
        },
        image_download_headers={
            "Referer": "https://www.pixiv.net/",
            "User-Agent": "PixivTestAgent/1.0",
        },
    )

    class NapCatDownloadBot(FakeBot):
        async def call_action(self, action, **params):
            self.actions.append((action, params))
            if action == "download_file":
                return {"data": {"file": f"/app/napcat/temp/{params['name']}"}}
            return None

    bot = NapCatDownloadBot()
    event = FakeEvent(
        bot=bot,
        raw_message={"group_id": 10001, "sender": {"nickname": "测试用户"}},
    )

    messages = await collect_results(
        monkeypatch,
        result,
        event=event,
        forward_mode="always",
    )

    assert messages == []
    assert [action for action, _ in bot.actions] == [
        "download_file",
        "download_file",
        "send_group_forward_msg",
    ]
    download_params = [
        params for action, params in bot.actions if action == "download_file"
    ]
    assert [params["url"] for params in download_params] == source_urls
    assert all(
        params["headers"]
        == [
            "Referer=https://www.pixiv.net/",
            "User-Agent=PixivTestAgent/1.0",
        ]
        for params in download_params
    )
    forward_params = bot.actions[-1][1]
    assert [
        node["data"]["content"][0]["data"]["file"]
        for node in forward_params["messages"][:2]
    ] == ["/app/napcat/temp/pixiv-0.jpg", "/app/napcat/temp/pixiv-1.jpg"]
    assert not any(image_path.exists() for image_path in image_paths)


@pytest.mark.asyncio
async def test_pixiv_forward_falls_back_to_single_image_upload_when_url_terminates(
    monkeypatch, tmp_path
):
    image_paths = [tmp_path / f"pixiv-{index}.jpg" for index in range(2)]
    source_urls = [
        f"https://i.pximg.net/img-original/pixiv-{index}.jpg" for index in range(2)
    ]
    for image_path in image_paths:
        image_path.write_bytes(b"pixiv-image")
    result = ParseResult(
        platform="pixiv",
        title="Pixiv作品",
        image_urls=[str(image_path) for image_path in image_paths],
        temporary_files=image_paths,
        image_source_urls={
            str(image_path.resolve()): source_url
            for image_path, source_url in zip(image_paths, source_urls, strict=True)
        },
        image_download_headers={"Referer": "https://www.pixiv.net/"},
    )

    class TerminatedDownloadBot(FakeBot):
        async def call_action(self, action, **params):
            self.actions.append((action, params))
            if action == "download_file" and "url" in params:
                raise RuntimeError("terminated")
            if action == "download_file":
                return {"data": {"file": f"/app/napcat/temp/{params['name']}"}}
            return None

    bot = TerminatedDownloadBot()
    event = FakeEvent(
        bot=bot,
        raw_message={"group_id": 10001, "sender": {"nickname": "测试用户"}},
    )

    messages = await collect_results(
        monkeypatch,
        result,
        event=event,
        forward_mode="always",
    )

    assert messages == []
    download_params = [
        params for action, params in bot.actions if action == "download_file"
    ]
    assert len(download_params) == 4
    assert [params["url"] for params in download_params[::2]] == source_urls
    assert all(
        params["headers"] == ["Referer=https://www.pixiv.net/"]
        for params in download_params[::2]
    )
    assert all(
        params["base64"] == "cGl4aXYtaW1hZ2U=" for params in download_params[1::2]
    )
    assert bot.actions[-1][0] == "send_group_forward_msg"
    assert [
        node["data"]["content"][0]["data"]["file"]
        for node in bot.actions[-1][1]["messages"][:2]
    ] == ["/app/napcat/temp/pixiv-0.jpg", "/app/napcat/temp/pixiv-1.jpg"]
    assert not any(image_path.exists() for image_path in image_paths)


@pytest.mark.asyncio
async def test_onebot_result_can_disable_forward_without_splitting(monkeypatch):
    result = ParseResult(
        platform="test",
        title="标题",
        image_urls=["base64://1", "base64://2", "base64://3"],
        disable_onebot_forward=True,
    )

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="always",
    )

    assert len(messages) == 1
    assert [type(component) for component in messages[0]] == [
        Image,
        Image,
        Image,
        Plain,
    ]


@pytest.mark.asyncio
async def test_onebot_delivery_flags_do_not_change_other_adapters(monkeypatch):
    result = ParseResult(
        platform="test",
        title="标题",
        image_urls=["base64://1"],
        disable_onebot_forward=True,
        split_media_for_onebot=True,
    )
    event = FakeEvent(platform_name="satori")

    messages = await collect_results(
        monkeypatch,
        result,
        event=event,
        forward_mode="always",
    )

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)


@pytest.mark.asyncio
async def test_description_without_images_stays_in_plain_message(monkeypatch):
    result = ParseResult(platform="test", description="只有简介")

    messages = await collect_results(monkeypatch, result)

    assert len(messages) == 1
    assert len(messages[0]) == 1
    assert isinstance(messages[0][0], Plain)
    assert messages[0][0].text == "简介:\n只有简介"


def test_onebot_direct_delivery_merges_summary_and_leading_text():
    result = ParseResult(
        platform="xiaoheihe",
        title="新拍照功能太权威了",
        author="Deepsucker",
        ordered_contents=[
            OrderedContent(
                kind="text",
                value="光是自定义站位和姿势就已经够爽了",
            )
        ],
    )

    messages = DeliveryService({"forward_mode": "never"}).build_content_results(
        FakeEvent(),
        result,
        include_video_url=False,
    )

    assert len(messages) == 1
    assert len(messages[0]) == 1
    assert isinstance(messages[0][0], Plain)
    assert messages[0][0].text == (
        "新拍照功能太权威了\n作者: Deepsucker\n光是自定义站位和姿势就已经够爽了"
    )


@pytest.mark.asyncio
async def test_ordered_text_success_failure_success_preserves_component_order(
    monkeypatch,
):
    result = ParseResult(
        platform="test",
        title="摘要",
        ordered_contents=[
            OrderedContent("text", "正文一"),
            OrderedContent("image", "base64://1"),
            OrderedContent("image_error", "第 2 张图片获取失败"),
            OrderedContent("image", "base64://3"),
            OrderedContent("text", "正文二"),
        ],
    )

    messages = await collect_results(monkeypatch, result)

    nodes = messages[0][0].nodes
    assert [type(node.content[0]) for node in nodes] == [
        Plain,
        Image,
        Plain,
        Image,
        Plain,
    ]
    assert [
        component.text if isinstance(component, Plain) else component.file
        for node in nodes
        for component in node.content
    ] == [
        "摘要\n正文一",
        "base64://1",
        "第 2 张图片获取失败",
        "base64://3",
        "正文二",
    ]


@pytest.mark.asyncio
async def test_empty_summary_sends_only_nodes(monkeypatch):
    result = ParseResult(
        platform="test",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )

    messages = await collect_results(monkeypatch, result)

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sender", "sender_name", "sender_id", "expected_name", "expected_id"),
    [
        ({"card": "群名片", "nickname": "原始昵称"}, "公开昵称", 123, "群名片", "123"),
        ({"card": "", "nickname": "原始昵称"}, "公开昵称", 456, "公开昵称", "456"),
        ({}, "", 789, "789", "789"),
    ],
)
async def test_forward_nodes_use_sender_name_fallbacks(
    monkeypatch, sender, sender_name, sender_id, expected_name, expected_id
):
    result = ParseResult(
        platform="test",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )
    event = FakeEvent(
        sender_id=sender_id,
        sender_name=sender_name,
        sender=sender,
        platform_name="satori",
    )

    messages = await collect_results(monkeypatch, result, event=event)

    for node in messages[0][0].nodes:
        assert node.name == expected_name
        assert node.uin == expected_id


@pytest.mark.asyncio
async def test_onebot_forward_nodes_use_qq_name_and_self_id(monkeypatch):
    result = ParseResult(
        platform="test",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )
    bot = FakeBot(
        responses={
            "get_login_info": {"user_id": 456, "nickname": "QQ机器人"},
        }
    )
    event = FakeEvent(
        bot=bot,
        sender_id=123,
        sender_name="消息发送者",
        platform_id="default",
        raw_message={"self_id": 456, "sender": {"nickname": "消息发送者"}},
    )

    messages = await collect_results(monkeypatch, result, event=event)

    for node in messages[0][0].nodes:
        assert node.name == "QQ机器人"
        assert node.uin == "456"
    assert bot.actions == [("get_login_info", {"self_id": 456})]


@pytest.mark.asyncio
async def test_non_dict_raw_message_uses_public_sender_name(monkeypatch):
    result = ParseResult(
        platform="test",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )
    event = FakeEvent(
        sender_id=123,
        sender_name=456,
        raw_message="not-onebot-json",
    )

    messages = await collect_results(monkeypatch, result, event=event)

    nodes = messages[0][0].nodes
    assert all(node.name == "456" and node.uin == "123" for node in nodes)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_name", ["telegram", "", "__raise__"])
async def test_unsupported_or_empty_platform_keeps_one_normal_chain(
    monkeypatch, platform_name
):
    result = ParseResult(
        platform="test",
        title="标题",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )
    event = FakeEvent(platform_name=platform_name)

    messages = await collect_results(
        monkeypatch,
        result,
        event=event,
        forward_mode="always",
    )

    assert len(messages) == 1
    assert [type(component) for component in messages[0]] == [
        Image,
        Image,
        Image,
        Plain,
    ]
    assert not any(isinstance(component, Nodes) for component in messages[0])


@pytest.mark.asyncio
async def test_satori_supports_forward_nodes(monkeypatch):
    result = ParseResult(
        platform="test",
        title="标题",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        event=FakeEvent(platform_name="satori"),
    )

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)


@pytest.mark.asyncio
async def test_reaction_is_skipped_outside_onebot():
    bot = FakeBot()
    event = FakeEvent(
        bot=bot,
        platform_name="telegram",
        raw_message={"message_id": 123},
    )

    await DeliveryService({"enable_parse_reaction": True}).react_success(event)

    assert bot.actions == []


@pytest.mark.asyncio
async def test_onebot_reaction_still_calls_configured_action():
    bot = FakeBot()
    event = FakeEvent(bot=bot, raw_message={"message_id": 123})

    await DeliveryService({"enable_parse_reaction": True}).react_success(event)

    assert bot.actions == [
        ("set_msg_emoji_like", {"message_id": 123, "emoji_id": "124"})
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("platform_name", ["telegram", "future_adapter"])
async def test_video_fallback_uses_plain_message_on_generic_platform(platform_name):
    bot = FakeBot()
    event = FakeEvent(bot=bot, platform_name=platform_name)
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        video_url="https://example.com/video.mp4",
    )

    await DeliveryService({}).send_forward_links(event, result, "视频超过大小限制")

    assert bot.actions == []
    assert len(event.sent) == 1
    assert len(event.sent[0]) == 1
    assert isinstance(event.sent[0][0], Plain)
    assert event.sent[0][0].text == (
        "测试平台 解析链接\n"
        "标题: 测试标题\n"
        "说明: 视频超过大小限制\n"
        "视频链接: https://example.com/video.mp4"
    )


@pytest.mark.asyncio
async def test_video_fallback_uses_nodes_on_satori():
    event = FakeEvent(platform_name="satori")
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        video_url="https://example.com/video.mp4",
    )

    await DeliveryService({}).send_forward_links(event, result, "视频超过大小限制")

    assert len(event.sent) == 1
    assert isinstance(event.sent[0][0], Nodes)
    assert [node.content[0].text for node in event.sent[0][0].nodes] == [
        "测试平台 解析链接\n标题: 测试标题\n说明: 视频超过大小限制",
        "视频直链:\nhttps://example.com/video.mp4",
    ]


def test_output_link_filter_is_disabled_by_default():
    result = ParseResult(
        platform="测试平台",
        description="正文 https://example.com/detail",
    )

    messages = DeliveryService({}).build_content_results(
        FakeEvent(platform_name="telegram"),
        result,
        include_video_url=False,
    )

    assert messages[0][0].text == "简介:\n正文 https://example.com/detail"


def test_output_link_filter_replaces_visible_links_with_configured_text():
    result = ParseResult(
        platform="测试平台",
        title="标题 https://example.com/title",
        description="正文 https://example.com/detail。",
        extra_lines=["更多 www.example.com/path"],
        ordered_contents=[
            OrderedContent("text", "段落 https://example.com/body?a=1&b=2")
        ],
    )

    messages = DeliveryService(
        {
            "filter_output_links": True,
            "filtered_link_text": "[打开原链接查看]",
            "forward_mode": "never",
        }
    ).build_content_results(
        FakeEvent(platform_name="telegram"),
        result,
        include_video_url=False,
    )

    assert [component.text for component in messages[0]] == [
        "标题 [打开原链接查看]\n简介:\n正文 [打开原链接查看]。\n更多 [打开原链接查看]",
        "段落 [打开原链接查看]",
    ]


@pytest.mark.asyncio
async def test_direct_link_fallback_is_not_filtered():
    event = FakeEvent(platform_name="telegram")
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        video_url="https://example.com/video.mp4",
    )

    await DeliveryService({"filter_output_links": True}).send_video_over_limit(
        event, result, "视频超过大小限制"
    )

    assert event.sent[0][0].text.endswith("视频链接: https://example.com/video.mp4")


@pytest.mark.asyncio
async def test_notice_fallback_does_not_include_video_url():
    event = FakeEvent(platform_name="telegram")
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        video_url="https://example.com/video.mp4",
    )

    await DeliveryService({"video_over_limit_action": "notice"}).send_video_over_limit(
        event, result, "视频超过大小限制"
    )

    assert event.sent == [[Plain("视频超过大小限制")]]


@pytest.mark.asyncio
async def test_group_file_falls_back_to_direct_link_outside_onebot_group():
    event = FakeEvent(platform_name="telegram")
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        video_url="https://example.com/video.mp4",
    )

    await DeliveryService(
        {"video_over_limit_action": "group_file"}
    ).send_video_over_limit(event, result, "视频超过大小限制")

    assert len(event.sent) == 1
    assert event.sent[0][0].text.endswith("视频链接: https://example.com/video.mp4")


@pytest.mark.asyncio
async def test_group_file_upload_failure_falls_back_to_direct_link():
    class UploadFailBot(FakeBot):
        async def call_action(self, action, **params):
            self.actions.append((action, params))
            if action == "upload_group_file":
                raise RuntimeError("upload failed")

    bot = UploadFailBot()
    event = FakeEvent(
        bot=bot,
        raw_message={"group_id": 456, "sender": {}},
    )
    result = ParseResult(
        platform="测试平台",
        title="测试标题",
        video_url="https://example.com/video.mp4",
    )
    delivery = DeliveryService({"video_over_limit_action": "group_file"})

    await delivery.send_video_over_limit(
        event,
        result,
        "视频超过大小限制",
    )

    assert bot.actions[0][0] == "upload_group_file"
    assert bot.actions[0][1]["file"] == result.video_url
    assert bot.actions[1][0] == "send_group_forward_msg"
    assert "https://example.com/video.mp4" in str(bot.actions[1][1]["messages"])


@pytest.mark.asyncio
async def test_group_file_upload_uses_remote_url_without_local_download():
    bot = FakeBot()
    event = FakeEvent(
        bot=bot,
        raw_message={"group_id": 456, "sender": {}},
    )
    result = ParseResult(
        platform="测试平台",
        title="测试/标题",
        video_url="https://example.com/video.mp4",
    )
    delivery = DeliveryService({"video_over_limit_action": "group_file"})

    await delivery.send_video_over_limit(
        event,
        result,
        "视频超过大小限制",
    )

    assert bot.actions == [
        (
            "upload_group_file",
            {
                "group_id": 456,
                "file": result.video_url,
                "name": "测试_标题.mp4",
            },
        )
    ]
    assert event.sent == []


@pytest.mark.asyncio
async def test_main_uses_notice_action_for_over_limit_video(monkeypatch):
    result = ParseResult(
        platform="test",
        title="摘要",
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result, video_over_limit_action="notice")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=51 * 1024 * 1024)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(
        plugin,
        FakeEvent(platform_name="telegram"),
    )

    texts = [message[0].text for message in messages]
    assert len(texts) == 2
    assert "摘要" in texts
    notice = next(text for text in texts if text != "摘要")
    assert "超过限制 50.00 MB" in notice
    assert "https://example.com/video.mp4" not in notice


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forward_mode", "failed_component_type"),
    [("never", Video), ("always", Nodes)],
    ids=["direct", "forward"],
)
async def test_main_uses_group_file_action_when_video_send_fails(
    monkeypatch,
    forward_mode,
    failed_component_type,
):
    class VideoSendFailEvent(FakeEvent):
        async def send(self, message):
            if any(
                isinstance(component, failed_component_type)
                for component in message.chain
            ):
                raise RuntimeError("video send failed")
            await super().send(message)

    result = ParseResult(
        platform="test",
        title="摘要",
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(
        result,
        forward_mode=forward_mode,
        max_video_size_mb=0,
        video_over_limit_action="group_file",
    )
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=100 * 1024 * 1024)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)
    bot = FakeBot()
    event = VideoSendFailEvent(
        bot=bot,
        raw_message={"group_id": 456, "sender": {}},
    )

    messages = await collect_plugin_results(plugin, event)

    assert bot.actions == [
        (
            "upload_group_file",
            {
                "group_id": 456,
                "file": result.video_url,
                "name": "摘要.mp4",
            },
        )
    ]
    assert not any(
        isinstance(component, Video) for message in messages for component in message
    )
    if failed_component_type is Nodes:
        assert any(
            "合并转发发送失败" in component.text
            for message in messages
            for component in message
            if isinstance(component, Plain)
        )
    assert event._has_send_oper is False


@pytest.mark.asyncio
async def test_notice_delivery_failure_still_hides_video_url():
    class SendFailEvent(FakeEvent):
        async def send(self, message):
            raise RuntimeError("send failed")

    plugin = make_plugin(
        ParseResult(platform="test"),
        video_over_limit_action="notice",
    )
    result = ParseResult(
        platform="test",
        video_url="https://example.com/video.mp4",
    )

    messages = [
        item
        async for item in plugin._forward_with_fallback(
            SendFailEvent(platform_name="telegram"),
            result,
            "视频超过大小限制",
        )
    ]

    assert len(messages) == 1
    assert "视频超过大小限制" in messages[0][0].text
    assert "https://example.com/video.mp4" not in messages[0][0].text


@pytest.mark.asyncio
async def test_always_mode_forwards_text_only_result(monkeypatch):
    messages = await collect_results(
        monkeypatch,
        ParseResult(platform="test", title="标题"),
        forward_mode="always",
    )

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)
    assert messages[0][0].nodes[0].content[0].text == "标题"


@pytest.mark.asyncio
async def test_never_mode_keeps_many_images_in_normal_chain(monkeypatch):
    result = ParseResult(
        platform="test",
        title="标题",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )

    messages = await collect_results(monkeypatch, result, forward_mode="never")

    assert len(messages) == 1
    assert [type(component) for component in messages[0]] == [
        Image,
        Image,
        Image,
        Plain,
    ]
    assert not any(isinstance(component, Nodes) for component in messages[0])


@pytest.mark.asyncio
@pytest.mark.parametrize(("length", "should_forward"), [(200, False), (201, True)])
async def test_text_threshold_is_strictly_greater(monkeypatch, length, should_forward):
    messages = await collect_results(
        monkeypatch,
        ParseResult(platform="test", title="字" * length),
        forward_mode="threshold",
        forward_image_threshold=99,
        forward_text_threshold=200,
    )

    assert isinstance(messages[0][0], Nodes) is should_forward


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "should_forward"), [(2, False), (3, True)])
async def test_image_threshold_is_strictly_greater(monkeypatch, count, should_forward):
    result = ParseResult(
        platform="test",
        image_urls=[f"base64://{index}" for index in range(count)],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="threshold",
        forward_image_threshold=2,
        forward_text_threshold=999,
    )

    assert isinstance(messages[0][0], Nodes) is should_forward


@pytest.mark.asyncio
async def test_text_threshold_counts_summary_and_ordered_body(monkeypatch):
    result = ParseResult(
        platform="test",
        title="题" * 100,
        ordered_contents=[OrderedContent("text", "文" * 101)],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="threshold",
        forward_image_threshold=99,
        forward_text_threshold=200,
    )

    assert isinstance(messages[0][0], Nodes)


@pytest.mark.asyncio
async def test_invalid_forward_mode_falls_back_to_threshold(monkeypatch):
    result = ParseResult(
        platform="test",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="unexpected",
    )

    assert isinstance(messages[0][0], Nodes)


@pytest.mark.asyncio
async def test_invalid_thresholds_fall_back_to_defaults(monkeypatch):
    result = ParseResult(
        platform="test",
        title="字" * 200,
        image_urls=["base64://1", "base64://2"],
    )

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="threshold",
        forward_image_threshold="invalid",
        forward_text_threshold=None,
    )

    assert not any(isinstance(component, Nodes) for component in messages[0])


@pytest.mark.asyncio
async def test_negative_thresholds_are_treated_as_zero(monkeypatch):
    result = ParseResult(platform="test", image_urls=["base64://1"])

    messages = await collect_results(
        monkeypatch,
        result,
        forward_mode="threshold",
        forward_image_threshold=-1,
        forward_text_threshold=-1,
    )

    assert isinstance(messages[0][0], Nodes)


@pytest.mark.asyncio
async def test_threshold_forward_keeps_regular_video_as_separate_message(monkeypatch):
    result = ParseResult(
        platform="test",
        title="摘要",
        image_urls=["base64://1", "base64://2", "base64://3"],
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result)
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=1024)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert len(messages) == 2
    assert isinstance(messages[0][0], Nodes)
    assert not any(isinstance(node.content[0], Video) for node in messages[0][0].nodes)
    assert isinstance(messages[1][0], Video)
    assert messages[1][0].file == result.video_url


@pytest.mark.asyncio
async def test_threshold_forward_keeps_xiaoheihe_game_video_inside(monkeypatch):
    result = ParseResult(
        platform="xiaoheihe",
        title="游戏详情",
        description="游戏简介",
        image_urls=["base64://1", "base64://2", "base64://3"],
        video_url="https://example.com/game.mp4",
        keep_video_in_forward=True,
    )
    plugin = make_plugin(result)
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=1024)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)
    assert isinstance(messages[0][0].nodes[-1].content[0], Video)
    assert messages[0][0].nodes[-1].content[0].file == result.video_url


@pytest.mark.asyncio
async def test_always_forward_keeps_regular_video_inside(monkeypatch):
    result = ParseResult(
        platform="test",
        title="摘要",
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result, forward_mode="always")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=1024)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert len(messages) == 1
    assert isinstance(messages[0][0], Nodes)
    assert isinstance(messages[0][0].nodes[-1].content[0], Video)


@pytest.mark.asyncio
async def test_forward_description_matches_plain_chain_format(monkeypatch):
    result = ParseResult(
        platform="test",
        title="标题",
        description="第一行\n第二行",
        image_urls=["base64://1", "base64://2", "base64://3"],
    )

    plain_messages = await collect_results(monkeypatch, result, forward_mode="never")
    forward_messages = await collect_results(monkeypatch, result)

    plain_chain = plain_messages[0]
    forward_chain = [node.content[0] for node in forward_messages[0][0].nodes]
    assert [type(component) for component in forward_chain] == [
        type(component) for component in plain_chain
    ]
    assert [
        component.text if isinstance(component, Plain) else component.file
        for component in forward_chain
    ] == [
        component.text if isinstance(component, Plain) else component.file
        for component in plain_chain
    ]
    assert forward_chain[-1].text == "标题\n简介:\n第一行\n第二行"


@pytest.mark.asyncio
async def test_non_forward_content_keeps_video_as_separate_message(monkeypatch):
    result = ParseResult(
        platform="test",
        title="summary",
        image_urls=["base64://1"],
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result, forward_mode="never")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=1024)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert len(messages) == 2
    assert not isinstance(messages[0][0], Nodes)
    assert isinstance(messages[1][0], Video)


@pytest.mark.asyncio
async def test_kook_materializes_remote_video_before_send(monkeypatch, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    result = ParseResult(
        platform="test",
        title="summary",
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result, forward_mode="never")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )
    converted_urls = []

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=1024)

    async def fake_convert_to_file_path(video):
        converted_urls.append(video.file)
        return str(video_path)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)
    monkeypatch.setattr(Video, "convert_to_file_path", fake_convert_to_file_path)

    messages = await collect_plugin_results(
        plugin,
        FakeEvent(platform_name="kook"),
    )

    assert converted_urls == [result.video_url]
    assert len(messages) == 2
    assert isinstance(messages[1][0], Video)
    assert messages[1][0].file == video_path.resolve().as_uri()
    assert messages[1][0].path == str(video_path.resolve())
    assert not video_path.exists()
    assert result.temporary_files == []


@pytest.mark.asyncio
async def test_kook_materializes_video_with_platform_headers(monkeypatch):
    requested_headers = []
    probed_headers = []
    probed_platforms = []
    materialized_platforms = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_headers.append(request.headers)
        return httpx.Response(
            200,
            content=b"video",
            headers={"Content-Type": "video/mp4", "Content-Length": "5"},
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(media.httpx, "AsyncClient", create_client)
    monkeypatch.setattr(
        media,
        "http_client_proxy_options",
        lambda config, platform_name: (
            materialized_platforms.append(platform_name) or {}
        ),
    )
    result = ParseResult(
        platform="bilibili",
        title="summary",
        video_url="https://upos-sz-estgoss.bilivideo.com/video.mp4",
        video_download_headers={
            "User-Agent": "BilibiliTestAgent/1.0",
            "Referer": "https://www.bilibili.com",
            "Cookie": "SESSDATA=must-not-leak",
            "Authorization": "must-not-leak",
        },
        video_download_host_suffixes=("bilivideo.com",),
    )
    plugin = make_plugin(result, forward_mode="never")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        probed_headers.append(headers)
        probed_platforms.append(platform_name)
        return VideoSizeInfo(size_bytes=5)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(
        plugin,
        FakeEvent(platform_name="kook"),
    )

    assert len(requested_headers) == 1
    assert probed_headers == [result.video_download_headers]
    assert probed_platforms == ["fake"]
    assert materialized_platforms == ["bilibili"]
    assert requested_headers[0]["User-Agent"] == "BilibiliTestAgent/1.0"
    assert requested_headers[0]["Referer"] == "https://www.bilibili.com"
    assert "Cookie" not in requested_headers[0]
    assert "Authorization" not in requested_headers[0]
    assert isinstance(messages[1][0], Video)
    assert messages[1][0].file.startswith("file:///")
    assert result.temporary_files == []


@pytest.mark.asyncio
async def test_kook_video_rejects_untrusted_download_redirect(monkeypatch):
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://evil.example/video.mp4"},
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(media.httpx, "AsyncClient", create_client)
    result = ParseResult(
        platform="bilibili",
        title="summary",
        video_url="https://upos-sz-estgoss.bilivideo.com/video.mp4",
        video_download_headers={"Referer": "https://www.bilibili.com"},
        video_download_host_suffixes=("bilivideo.com",),
    )
    plugin = make_plugin(result, forward_mode="never")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=5)

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)

    messages = await collect_plugin_results(
        plugin,
        FakeEvent(platform_name="kook"),
    )

    assert requested_urls == [result.video_url]
    assert not any(
        isinstance(component, Video) for message in messages for component in message
    )
    assert any(
        result.video_url in component.text
        for message in messages
        for component in message
        if isinstance(component, Plain)
    )


@pytest.mark.asyncio
async def test_kook_video_materialization_failure_falls_back_to_direct_link(
    monkeypatch,
):
    result = ParseResult(
        platform="test",
        title="summary",
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result, forward_mode="never")
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )

    async def fake_probe(url, headers=None, platform_name=""):
        return VideoSizeInfo(size_bytes=1024)

    async def fail_convert_to_file_path(video):
        raise httpx.ConnectError("download failed")

    monkeypatch.setattr(plugin, "_probe_video_size", fake_probe)
    monkeypatch.setattr(Video, "convert_to_file_path", fail_convert_to_file_path)

    messages = await collect_plugin_results(
        plugin,
        FakeEvent(platform_name="kook"),
    )

    assert not any(
        isinstance(component, Video) for message in messages for component in message
    )
    texts = [component.text for message in messages for component in message]
    assert "summary" in texts
    assert any("视频发送失败: ConnectError" in text for text in texts)
    assert any(result.video_url in text for text in texts)


@pytest.mark.asyncio
async def test_audio_is_sent_after_track_summary(monkeypatch):
    result = ParseResult(
        platform="douyin",
        title="歌曲标题",
        audio_url="https://v3-luna.douyinvod.com/song.m4a",
    )

    messages = await collect_results(monkeypatch, result, forward_mode="never")

    assert len(messages) == 2
    assert isinstance(messages[0][0], Plain)
    assert messages[0][0].text == "歌曲标题"
    assert isinstance(messages[1][0], Record)
    assert messages[1][0].file == result.audio_url


@pytest.mark.asyncio
async def test_video_url_is_only_in_summary_when_direct_send_is_disabled(
    monkeypatch,
):
    result = ParseResult(
        platform="test",
        title="摘要",
        image_urls=["base64://1", "base64://2", "base64://3"],
        video_url="https://example.com/video.mp4",
    )
    plugin = make_plugin(result, send_video_by_url=False)
    monkeypatch.setattr(
        main, "extract_context", lambda event: SimpleNamespace(combined_text="url")
    )
    forwarded = []

    async def fake_forward(event, parsed_result, reason):
        forwarded.append((parsed_result, reason))

    monkeypatch.setattr(plugin, "_send_forward_links", fake_forward)

    messages = await collect_plugin_results(plugin, FakeEvent())

    assert len(messages) == 1
    nodes = messages[0][0].nodes
    plain_nodes = [
        node.content[0] for node in nodes if isinstance(node.content[0], Plain)
    ]
    assert (
        sum(
            "视频链接: https://example.com/video.mp4" in component.text
            for component in plain_nodes
        )
        == 1
    )
    assert forwarded == []


@pytest.mark.asyncio
async def test_probe_range_reads_headers_without_buffering_response_body(monkeypatch):
    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("Range response body must not be read")
            yield b""

    def handler(request):
        if request.method == "HEAD":
            return httpx.Response(200, request=request)
        assert request.headers["Range"] == "bytes=0-0"
        return httpx.Response(
            206,
            headers={"Content-Length": "999999999"},
            content=FailingStream(),
            request=request,
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    plugin = MultiParserPlugin.__new__(MultiParserPlugin)
    plugin.config = {"size_check_timeout_seconds": 5}

    size_info = await plugin._probe_video_size("https://example.com/video.mp4")

    assert size_info.size_bytes == 999999999


@pytest.mark.asyncio
async def test_probe_does_not_treat_http_error_length_as_video_size(monkeypatch):
    requested_methods = []

    def handler(request):
        requested_methods.append(request.method)
        return httpx.Response(
            403,
            headers={"Content-Length": "507"},
            request=request,
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    plugin = MultiParserPlugin.__new__(MultiParserPlugin)
    plugin.config = {"size_check_timeout_seconds": 5}

    size_info = await plugin._probe_video_size("https://example.com/video.mp4")

    assert requested_methods == ["HEAD", "GET"]
    assert size_info.size_bytes is None
    assert "403" in size_info.reason
    assert "example.com" not in size_info.reason


@pytest.mark.asyncio
async def test_probe_uses_platform_headers_without_credentials(monkeypatch):
    requested_headers = []

    def handler(request):
        requested_headers.append(request.headers)
        return httpx.Response(
            200,
            headers={"Content-Length": "123"},
            request=request,
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    plugin = MultiParserPlugin.__new__(MultiParserPlugin)
    plugin.config = {"size_check_timeout_seconds": 5}

    size_info = await plugin._probe_video_size(
        "https://example.com/video.mp4",
        {
            "User-Agent": "PlatformAgent/1.0",
            "Referer": "https://example.com/source",
            "Cookie": "must-not-leak",
            "Authorization": "must-not-leak",
        },
    )

    assert size_info.size_bytes == 123
    assert requested_headers[0]["User-Agent"] == "PlatformAgent/1.0"
    assert requested_headers[0]["Referer"] == "https://example.com/source"
    assert "Cookie" not in requested_headers[0]
    assert "Authorization" not in requested_headers[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("head_headers", "range_headers", "expected_size"),
    [
        ({"Content-Length": "123"}, {}, 123),
        ({}, {"Content-Range": "bytes 0-0/456"}, 456),
    ],
)
async def test_probe_preserves_head_and_content_range_header_parsing(
    monkeypatch, head_headers, range_headers, expected_size
):
    def handler(request):
        if request.method == "HEAD":
            return httpx.Response(200, headers=head_headers, request=request)
        return httpx.Response(
            206,
            headers=range_headers,
            content=b"ignored",
            request=request,
        )

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        main.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    plugin = MultiParserPlugin.__new__(MultiParserPlugin)
    plugin.config = {"size_check_timeout_seconds": 5}

    size_info = await plugin._probe_video_size("https://example.com/video.mp4")

    assert size_info.size_bytes == expected_size
