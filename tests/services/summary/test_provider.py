from types import SimpleNamespace

import pytest
from astrbot_multi_parser.services.summary.provider import SummaryProviderResolver


class Context:
    def __init__(self):
        self.provider = object()
        self.selected = {}

    def get_provider_by_id(self, provider_id):
        return self.selected.get(provider_id)

    async def get_using_provider_async(self, origin):
        return self.provider


@pytest.mark.asyncio
async def test_summary_provider_prefers_modality_provider_and_falls_back_to_text():
    context = Context()
    text_provider = object()
    context.selected["text"] = text_provider
    resolver = SummaryProviderResolver(
        context,
        {"ai_summary_text_provider_id": "text"},
    )
    event = SimpleNamespace(unified_msg_origin="test")

    assert await resolver.resolve(event, "vision") is text_provider
    assert await resolver.resolve(event, "text") is text_provider
