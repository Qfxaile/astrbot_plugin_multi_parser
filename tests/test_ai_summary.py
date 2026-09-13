from types import SimpleNamespace

import pytest
from astrbot_multi_parser.core.contracts import OrderedContent, ParseResult
from astrbot_multi_parser.services.ai_summary import DEFAULT_PROMPT, AISummaryService


class FakeProvider:
    def __init__(self, text="总结内容"):
        self.text = text
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(completion_text=self.text)


class FakeContext:
    def __init__(self, provider):
        self.provider = provider
        self.by_id = {}

    def get_provider_by_id(self, provider_id):
        return self.by_id.get(provider_id)

    async def get_using_provider_async(self, umo):
        return self.provider


class FakeEvent:
    unified_msg_origin = "test:private:user"


@pytest.mark.asyncio
async def test_summary_is_disabled_by_default():
    provider = FakeProvider()
    service = AISummaryService(FakeContext(provider), {})

    assert await service.summarize(FakeEvent(), ParseResult(platform="测试")) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_text_summary_uses_current_provider_and_default_prompt():
    provider = FakeProvider()
    service = AISummaryService(
        FakeContext(provider),
        {"enable_ai_summary": True, "ai_summary_mode": "text_only"},
    )
    result = ParseResult(
        platform="测试平台",
        title="标题",
        author="作者",
        description="简介",
        ordered_contents=[OrderedContent("text", "正文")],
    )

    assert await service.summarize(FakeEvent(), result) == ["总结内容"]
    assert len(provider.calls) == 1
    assert provider.calls[0]["image_urls"] is None
    assert "标题" in provider.calls[0]["prompt"]
    assert DEFAULT_PROMPT.splitlines()[0] in provider.calls[0]["prompt"]
    assert "不要使用 Markdown" in provider.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_all_mode_skips_empty_subtitles_and_uses_configured_provider():
    current = FakeProvider("正文总结")
    subtitle = FakeProvider("字幕总结")
    context = FakeContext(current)
    context.by_id["subtitle-provider"] = subtitle
    service = AISummaryService(
        context,
        {
            "enable_ai_summary": True,
            "ai_summary_mode": "all",
            "ai_summary_subtitle_provider_id": "subtitle-provider",
        },
    )

    result = ParseResult(platform="测试", subtitle_text="  字幕文本  ")
    assert await service.summarize(FakeEvent(), result) == ["正文总结", "字幕总结"]
    assert len(current.calls) == 1
    assert len(subtitle.calls) == 1
    assert "字幕文本" in subtitle.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_vision_without_provider_id_falls_back_to_text_provider():
    current = FakeProvider("当前模型")
    text = FakeProvider("文本模型")
    context = FakeContext(current)
    context.by_id["text-provider"] = text
    service = AISummaryService(
        context,
        {
            "enable_ai_summary": True,
            "ai_summary_mode": "text_and_images",
            "ai_summary_text_provider_id": "text-provider",
        },
    )
    assert await service._provider(FakeEvent(), "vision") is text
    result = ParseResult(platform="测试")
    assert await service.summarize(FakeEvent(), result) == ["文本模型"]
    assert len(text.calls) == 1
    assert current.calls == []


@pytest.mark.asyncio
async def test_invalid_prompt_and_provider_failure_are_silent():
    provider = FakeProvider()
    service = AISummaryService(
        FakeContext(provider),
        {
            "enable_ai_summary": True,
            "ai_summary_prompt": "{unknown}",
        },
    )
    assert await service.summarize(FakeEvent(), ParseResult(platform="测试")) == []

    service.context = FakeContext(None)
    assert await service.summarize(FakeEvent(), ParseResult(platform="测试")) == []
