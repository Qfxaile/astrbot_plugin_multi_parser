from types import SimpleNamespace

import pytest
from astrbot_multi_parser.core.contracts import OrderedContent
from astrbot_multi_parser.services.observability import PluginObservability
from astrbot_multi_parser.services.summary.service import (
    DEFAULT_PROMPT,
    AISummaryService,
)
from result_factory import build_result


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

    assert await service.summarize(FakeEvent(), build_result(platform="测试")) == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_text_summary_uses_current_provider_and_default_prompt():
    provider = FakeProvider()
    service = AISummaryService(
        FakeContext(provider),
        {"enable_ai_summary": True, "ai_summary_mode": "text_only"},
    )
    result = build_result(
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
async def test_summary_records_exact_provider_usage_when_available(tmp_path):
    from astrbot.core.provider.entities import TokenUsage

    class UsageProvider(FakeProvider):
        async def text_chat(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                completion_text=self.text,
                usage=TokenUsage(input_other=20, input_cached=3, output=5),
            )

    observability = PluginObservability(tmp_path / "observability.sqlite3")
    service = AISummaryService(
        FakeContext(UsageProvider()),
        {"enable_ai_summary": True},
        observability,
    )

    assert await service.summarize(FakeEvent(), build_result(platform="测试")) == [
        "总结内容"
    ]
    await observability.flush()
    totals = (await observability.dashboard())["totals"]
    assert totals["tokens"] == 28
    assert totals["exact_token_calls"] == 1


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
    result = build_result(platform="测试")
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
    assert await service.summarize(FakeEvent(), build_result(platform="测试")) == []


@pytest.mark.asyncio
async def test_invalid_summary_configuration_uses_safe_defaults():
    provider = FakeProvider()
    service = AISummaryService(
        FakeContext(provider),
        {
            "enable_ai_summary": "true",
            "ai_summary_mode": "invalid",
            "ai_summary_max_input_chars": "invalid",
            "ai_summary_timeout_seconds": "invalid",
        },
    )

    assert service.enabled() is True
    assert service.mode() == "text_only"
    assert service._max_chars() == 30000
    assert await service.summarize(FakeEvent(), build_result(platform="测试")) == [
        "总结内容"
    ]

    service.context = FakeContext(None)
    assert await service.summarize(FakeEvent(), build_result(platform="测试")) == []
