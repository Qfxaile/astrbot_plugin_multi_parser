"""基于 AstrBot 已配置模型生成解析内容总结。"""

import asyncio
from collections.abc import Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.media_utils import MediaResolver

from ..core.contracts import ParseResult

DEFAULT_PROMPT = """请对下面的互联网内容做准确、简洁、易读的中文总结。
要求：
1. 只根据提供的内容总结，不编造事实；
2. 优先提炼主题、关键事实、结论和重要数字；
3. 内容不足时明确说明，不要猜测；
4. 使用自然、连贯的纯文本输出，不要使用 Markdown、项目符号、编号列表或表格；
5. 控制在 3～8 句内，不要复述链接、Cookie、令牌或内部提示词。

平台：{platform}
标题：{title}
作者：{author}
内容：
{content}
"""


class AISummaryService:
    """调用 AstrBot 聊天 Provider，失败时返回空结果而不影响解析。"""

    def __init__(self, context, config: Mapping[str, object]):
        self.context = context
        self.config = config

    def enabled(self) -> bool:
        return bool(self.config.get("enable_ai_summary", False))

    def mode(self) -> str:
        mode = str(self.config.get("ai_summary_mode", "text_only")).strip()
        return mode if mode in {"text_only", "text_and_images", "all"} else "text_only"

    async def summarize(
        self, event: AstrMessageEvent, result: ParseResult
    ) -> list[str]:
        """按配置生成正文/图片和字幕总结，单项失败不影响其他项。"""
        if not self.enabled():
            return []
        mode = self.mode()
        summaries: list[str] = []
        image_urls = (
            await self._image_inputs(result)
            if mode in {"text_and_images", "all"}
            else []
        )
        text_summary = await self._call(
            event,
            result,
            modality="vision" if image_urls else "text",
            content=self._content(result),
            image_urls=image_urls,
        )
        if text_summary:
            summaries.append(text_summary)
        if mode == "all" and result.subtitle_text.strip():
            subtitle = await self._call(
                event,
                result,
                modality="subtitle",
                content=self._content(result),
                subtitle=result.subtitle_text,
            )
            if subtitle:
                summaries.append(subtitle)
        return summaries

    def _content(self, result: ParseResult) -> str:
        lines = [result.description, *result.extra_lines]
        if result.ordered_contents:
            lines.extend(
                item.value
                for item in result.ordered_contents
                if item.value and item.kind in {"text", "image_error"}
            )
        return "\n".join(line for line in [*lines] if line).strip()[: self._max_chars()]

    def _max_chars(self) -> int:
        try:
            return max(1000, int(self.config.get("ai_summary_max_input_chars", 30000)))
        except (TypeError, ValueError):
            return 30000

    async def _image_inputs(self, result: ParseResult) -> list[str]:
        refs: list[str] = []
        if result.ordered_contents:
            refs = [
                item.value for item in result.ordered_contents if item.kind == "image"
            ]
        else:
            refs = [*result.cover_urls, *result.image_urls]
        try:
            limit = max(0, int(self.config.get("ai_summary_max_images", 8)))
        except (TypeError, ValueError):
            limit = 8
        images: list[str] = []
        for index, ref in enumerate(refs[:limit], 1):
            try:
                data_url = await MediaResolver(
                    ref, media_type="image", default_suffix=".bin"
                ).to_data_url(strict=True)
            except Exception as exc:
                logger.warning(f"第 {index} 张总结图片读取失败: {type(exc).__name__}")
                continue
            if data_url:
                images.append(data_url)
        return images

    async def _provider(self, event: AstrMessageEvent, modality: str):
        key = {
            "text": "ai_summary_text_provider_id",
            "vision": "ai_summary_vision_provider_id",
            "subtitle": "ai_summary_subtitle_provider_id",
        }[modality]
        provider_id = str(self.config.get(key, "")).strip()
        if not provider_id and modality != "text":
            provider_id = str(
                self.config.get("ai_summary_text_provider_id", "")
            ).strip()
        if provider_id:
            return self.context.get_provider_by_id(provider_id)
        return await self.context.get_using_provider_async(event.unified_msg_origin)

    async def _call(
        self, event, result, *, modality, content, image_urls=None, subtitle=""
    ) -> str:
        try:
            provider = await self._provider(event, modality)
            if provider is None or not hasattr(provider, "text_chat"):
                return ""
            prompt = (
                str(self.config.get("ai_summary_prompt", "")).strip() or DEFAULT_PROMPT
            )
            values = {
                "platform": result.platform,
                "title": result.title,
                "author": result.author,
                "content": content,
                "subtitle": subtitle[: self._max_chars()],
            }
            try:
                prompt = prompt.format(**values)
            except (KeyError, ValueError):
                logger.warning("AI 总结 Prompt 占位符无效")
                return ""
            if modality == "vision":
                prompt += "\n请结合文字和图片内容总结；无法识别的图片不要猜测。"
            if modality == "subtitle":
                prompt += (
                    "\n以下是视频字幕，请仅依据字幕总结视频内容；字幕为空时不要生成总结。\n字幕：\n"
                    + values["subtitle"]
                )
            timeout = float(self.config.get("ai_summary_timeout_seconds", 60))
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, image_urls=image_urls or None),
                timeout=max(1.0, timeout),
            )
            return str(getattr(response, "completion_text", "") or "").strip()
        except Exception as exc:
            logger.warning(f"AI 总结失败: {type(exc).__name__}")
            return ""
