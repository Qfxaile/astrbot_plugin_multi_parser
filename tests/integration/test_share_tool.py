import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from astrbot.api.message_components import Json, Plain, Reply
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.core.agent.hooks import BaseAgentRunHooks
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.runners.tool_loop_agent_runner import ToolLoopAgentRunner
from astrbot.core.agent.tool_image_cache import tool_image_cache
from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor
from astrbot_multi_parser.main import MultiParserPlugin
from httpx import AsyncClient

from .test_main import TEST_PNG, FakeBot, FakeEvent

ARTICLE = "https://mp.weixin.qq.com/s/sample"
NOTE = "https://www.xiaohongshu.com/explore/note?xsec_token=A%2BB%3D&xsec_source=share"
IMAGES = [f"https://mmbiz.qpic.cn/test/{i}.png" for i in range(1, 4)]


def message(text, *parts):
    event = FakeEvent(bot=FakeBot(), raw_message={"group_id": "88"})
    event.message_str = text
    event.get_messages = lambda: list(parts)
    event.unified_msg_origin = "qq:GroupMessage:88"
    return event


async def offered(plugin, event, cid="one"):
    req = ProviderRequest(conversation=SimpleNamespace(cid=cid))
    await plugin.offer_share_reader(event, req)
    return req.func_tool.get_tool("read_shared_link") if req.func_tool else None


async def invoke(tool, event, *, timeout=10, **args):
    ctx = SimpleNamespace(
        context=SimpleNamespace(event=event), tool_call_timeout=timeout
    )
    results = [value async for value in FunctionToolExecutor.execute(tool, ctx, **args)]
    assert len(results) == 1
    return results[0].content


@pytest.fixture
def reader(tmp_path, monkeypatch, request):
    article_images = getattr(request, "param", IMAGES)
    urls = []

    def handle(request):
        url = str(request.url)
        urls.append(url)
        if url == ARTICLE:
            tags = "".join(f'<img data-src="{src}">' for src in article_images)
            return httpx.Response(
                200, text=f'<div id="js_content">依据是原始测量数据。{tags}</div>'
            )
        if url == NOTE:
            state = {
                "note": {
                    "noteDetailMap": {
                        "note": {
                            "note": {
                                "title": "原笔记",
                                "desc": "现场记录",
                                "imageList": [],
                            }
                        }
                    }
                }
            }
            return httpx.Response(
                200,
                text=f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>",
            )
        if url in IMAGES:
            return httpx.Response(
                200, content=TEST_PNG, headers={"Content-Type": "image/png"}
            )
        raise AssertionError(url)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: AsyncClient(transport=httpx.MockTransport(handle), **kw),
    )
    plugin = MultiParserPlugin(
        None,
        {
            "parse_mode": "tool",
            "image_temp_dir": str(tmp_path),
            "enable_conversation_history": True,
        },
    )
    return plugin, urls


@pytest.mark.parametrize("url", [ARTICLE, NOTE])
async def test_passive_share_has_no_effect(reader, monkeypatch, url):
    plugin, urls = reader
    event = message(url)
    for name in ("record_history", "react_success", "summarize"):
        monkeypatch.setattr(plugin, name, AsyncMock(side_effect=AssertionError(name)))
    assert [item async for item in plugin.handle_parse(event)] == []
    assert await offered(plugin, event) is None
    assert not urls and not event.sent and not event.bot.actions
    assert not event._has_send_oper


async def test_direct_question_keeps_full_share_parameters(reader):
    plugin, urls = reader
    event = message(f"{NOTE} 这条笔记讲什么？")
    tool = await offered(plugin, event)
    assert not urls
    [text] = await invoke(tool, event)
    assert "现场记录" in text.text
    assert urls == [NOTE]
    plugin.config["parse_mode"] = "auto"
    assert await offered(plugin, event) is None


@pytest.mark.parametrize(
    ("url", "question", "expected"),
    [
        (ARTICLE, "，这篇讲什么？", "测量"),
        (ARTICLE, "这篇讲什么？", "测量"),
        (NOTE, "，这条讲什么？", "现场记录"),
    ],
)
async def test_question_immediately_after_link(reader, url, question, expected):
    plugin, urls = reader
    event = message(url + question)
    tool = await offered(plugin, event)
    assert tool is not None
    [text] = await invoke(tool, event)
    assert expected in text.text
    assert urls == [url]


@pytest.mark.parametrize("reader", [[IMAGES[0], IMAGES[0], IMAGES[1]]], indirect=True)
@pytest.mark.parametrize("number", [0, 1, 2, 3, 4])
async def test_repeated_article_image_slots(reader, tmp_path, number):
    plugin, urls = reader
    event = message("看看这篇", Reply(id=73, chain=[Plain(ARTICLE)]))
    content = await invoke(await offered(plugin, event), event, image_number=number)
    if 1 <= number <= 3:
        label, image = content
        assert f"第 {number} 张" in label.text
        assert base64.b64decode(image.data) == TEST_PNG
        assert urls == [ARTICLE, IMAGES[1 if number == 3 else 0]]
    else:
        assert "3 张" in content[0].text
        assert urls == [ARTICLE]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("during_close", [False, True])
async def test_tool_timeout_cleans_images(reader, tmp_path, monkeypatch, during_close):
    plugin, _ = reader
    started = asyncio.Event()

    class SlowClient(AsyncClient):
        async def __aexit__(self, *args):
            await super().__aexit__(*args)
            if during_close:
                started.set()
                await asyncio.Event().wait()

    class SlowImage(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 65536
            started.set()
            await asyncio.Event().wait()

    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(
                200, text=f'<div id="js_content"><img src="{IMAGES[0]}"></div>'
            )
        assert str(request.url) == IMAGES[0]
        return (
            httpx.Response(200, content=TEST_PNG)
            if during_close
            else httpx.Response(200, stream=SlowImage())
        )

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: SlowClient(transport=httpx.MockTransport(handler), **kw),
    )
    event = message("看图", Reply(id=73, chain=[Plain(ARTICLE)]))
    with pytest.raises(Exception, match="execution timeout"):
        await invoke(await offered(plugin, event), event, timeout=0.2, image_number=1)
    assert started.is_set()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "quoted",
    [
        Reply(id=73, chain=[Plain(ARTICLE)]),
        Reply(id=73, chain=[Json(data={"meta": {"news": {"jumpUrl": ARTICLE}}})]),
    ],
)
async def test_quoted_question_and_followup_image(reader, tmp_path, quoted):
    plugin, urls = reader
    event = message("这篇文章的依据是什么？", quoted)
    tool = await offered(plugin, event)
    assert not urls and not event.bot.actions
    [text] = await invoke(tool, event)
    assert "依据是原始测量数据" in text.text and "3 张" in text.text
    assert urls == [ARTICLE]
    next_event = message("第二张图里是谁？")
    assert await offered(plugin, next_event, cid="other") is None
    tool = await offered(plugin, next_event)
    label, image = await invoke(tool, next_event, image_number=2)
    assert "第 2 张" in label.text
    assert image.type == "image" and base64.b64decode(image.data) == TEST_PNG
    assert image.mimeType == "image/png"
    assert urls == [ARTICLE, ARTICLE, IMAGES[1]]
    assert not list(tmp_path.iterdir())
    assert not event.sent and not next_event.sent


async def test_id_only_quote_resolved_on_call(reader):
    plugin, urls = reader
    event = message("依据是什么？", Reply(id=73))
    event.bot.responses["get_msg"] = {
        "group_id": 88,
        "message": [{"type": "text", "data": {"text": ARTICLE}}],
    }
    tool = await offered(plugin, event)
    assert not urls and not event.bot.actions
    [text] = await invoke(tool, event)
    assert "测量" in text.text and urls == [ARTICLE]
    assert event.bot.actions == [("get_msg", {"message_id": 73})]
    event.bot.responses["get_msg"]["group_id"] = 89
    [error] = await invoke(tool, event)
    assert "无法取得" in error.text and urls == [ARTICLE]


async def test_invalid_index_and_disabled_platform(reader):
    plugin, urls = reader
    event = message(f"看看第一张图 {ARTICLE}")
    tool = await offered(plugin, event)
    [error] = await invoke(tool, event, image_number=-1)
    assert "非负整数" in error.text and not urls
    [error] = await invoke(tool, event, image_number=6)
    assert "只有 3 张" in error.text and urls == [ARTICLE]
    plugin.config["platform_switches"] = {"wechat": False}
    assert await offered(plugin, event) is None


async def test_runner_places_only_requested_image_in_user_role(
    reader, tmp_path, monkeypatch
):
    plugin, urls = reader
    event = message("第一张图是什么？", Reply(id=73, chain=[Plain(ARTICLE)]))
    req = ProviderRequest(prompt=event.message_str)
    await plugin.offer_share_reader(event, req)
    mock_chat = AsyncMock(
        side_effect=[
            LLMResponse(
                role="assistant",
                tools_call_name=["read_shared_link"],
                tools_call_args=[{"image_number": 1}],
                tools_call_ids=["share_1"],
            ),
            LLMResponse(role="assistant", completion_text="读图完成"),
        ]
    )
    provider = SimpleNamespace(
        provider_config={"id": "fixture", "modalities": ["tool_use", "image"]},
        text_chat=mock_chat,
    )
    monkeypatch.setattr(
        tool_image_cache, "_cache_dir", str(tmp_path / "framework-cache")
    )
    runner = ToolLoopAgentRunner()
    await runner.reset(
        provider=provider,
        request=req,
        run_context=ContextWrapper(context=SimpleNamespace(event=event)),
        agent_hooks=BaseAgentRunHooks(),
        tool_executor=FunctionToolExecutor,
        streaming=False,
    )
    async for _ in runner.step_until_done(3):
        pass
    assert runner.final_llm_resp.completion_text == "读图完成"
    payload = mock_chat.await_args.kwargs["contexts"]
    images = [
        (msg["role"], part["image_url"]["url"])
        for msg in payload
        if isinstance(msg["content"], list)
        for part in msg["content"]
        if part["type"] == "image_url"
    ]
    assert images == [
        ("user", "data:image/png;base64," + base64.b64encode(TEST_PNG).decode())
    ]
    assert urls == [ARTICLE, IMAGES[0]]


async def test_parser_error_is_private_and_does_not_leak_url(reader, monkeypatch):
    plugin, urls = reader
    event = message(f"看看 {NOTE}")
    monkeypatch.setattr(
        plugin.parsers["redbook"], "parse", AsyncMock(side_effect=ValueError(NOTE))
    )
    [error] = await invoke(await offered(plugin, event), event)
    assert "读取失败" in error.text and NOTE not in error.text
    assert await offered(plugin, message("再看看图片")) is None
    assert not event.sent and not urls
