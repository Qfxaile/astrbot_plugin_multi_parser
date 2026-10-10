"""AI 总结 Provider 选择策略。"""

from collections.abc import Mapping

from ...core.settings import PluginSettings


class SummaryProviderResolver:
    """按总结模态选择指定 Provider，缺省回退到当前会话模型。"""

    def __init__(self, context, config: Mapping[str, object]):
        self.context = context
        self.settings = PluginSettings(config)

    async def resolve(self, event, modality: str):
        key = {
            "text": "ai_summary_text_provider_id",
            "vision": "ai_summary_vision_provider_id",
        }[modality]
        provider_id = self.settings.text(key)
        if not provider_id and modality != "text":
            provider_id = self.settings.text("ai_summary_text_provider_id")
        if provider_id:
            return self.context.get_provider_by_id(provider_id)
        return await self.context.get_using_provider_async(event.unified_msg_origin)
