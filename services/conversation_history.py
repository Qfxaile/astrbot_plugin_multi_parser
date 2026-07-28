"""将插件解析输出记录到 AstrBot 的 LLM 会话历史。"""

from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.utils.media_utils import MediaResolver

from ..core.contracts import ParseResult


class ConversationHistoryService:
    """使用 AstrBot 公开会话接口保存解析产生的用户与助手消息。"""

    def __init__(self, conversation_manager: Any):
        self.conversation_manager = conversation_manager

    async def record_parse_result(
        self,
        event: AstrMessageEvent,
        source_text: str,
        result: ParseResult,
        *,
        include_images: bool = False,
    ) -> None:
        """尽力记录成功解析结果，历史写入失败不影响已经完成的消息投递。"""
        try:
            conversation_id = await self.conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            if not conversation_id:
                conversation_id = await self.conversation_manager.new_conversation(
                    event.unified_msg_origin,
                    platform_id=event.get_platform_id(),
                )
            await self.conversation_manager.add_message_pair(
                conversation_id,
                {"role": "user", "content": source_text},
                {
                    "role": "assistant",
                    "content": await build_parse_history_content(
                        result,
                        include_images=include_images,
                    ),
                },
            )
        except Exception as exc:
            logger.warning(f"解析结果写入 LLM 会话失败: {type(exc).__name__}")


async def build_parse_history_content(
    result: ParseResult,
    *,
    include_images: bool = False,
) -> str | list[dict]:
    """生成不泄漏媒体地址或本地路径的文本、多模态解析历史。"""
    if not include_images:
        return _build_text_only_content(result)

    parts: list[dict] = [{"type": "text", "text": _build_summary(result)}]
    contains_image = False

    if result.ordered_contents:
        image_number = 0
        for item in result.ordered_contents:
            if not item.value:
                continue
            if item.kind != "image":
                parts.append({"type": "text", "text": item.value})
                continue

            image_number += 1
            image_part = await _build_image_part(item.value, image_number)
            if image_part is None:
                parts.append(
                    {
                        "type": "text",
                        "text": f"[第 {image_number} 张图片未能写入会话]",
                    }
                )
                continue
            parts.append(image_part)
            contains_image = True
    else:
        image_number = 0
        for index, image_ref in enumerate([*result.cover_urls, *result.image_urls]):
            image_number += 1
            if image_ref:
                image_part = await _build_image_part(image_ref, image_number)
                if image_part is not None:
                    parts.append(image_part)
                    contains_image = True
                    continue
            error = result.image_errors.get(index)
            if not image_ref and not error:
                continue
            parts.append(
                {
                    "type": "text",
                    "text": error or f"[第 {image_number} 张图片未能写入会话]",
                }
            )

    media_status = _build_media_status(result)
    if media_status:
        parts.append({"type": "text", "text": media_status})

    if contains_image:
        return parts
    return "\n".join(part["text"] for part in parts)


def _build_text_only_content(result: ParseResult) -> str:
    """生成仅包含文本、图片数量和媒体发送状态的解析历史。"""
    lines = [_build_summary(result)]
    if result.ordered_contents:
        lines.extend(
            item.value
            for item in result.ordered_contents
            if item.value and item.kind in {"text", "image_error"}
        )
        image_count = sum(
            bool(item.value) and item.kind == "image"
            for item in result.ordered_contents
        )
    else:
        lines.extend(error for error in result.image_errors.values() if error)
        image_count = sum(bool(value) for value in result.cover_urls)
        image_count += sum(bool(value) for value in result.image_urls)

    if image_count:
        lines.append(f"图片: {image_count} 张")
    if media_status := _build_media_status(result):
        lines.append(media_status)
    return "\n".join(lines)


def _build_summary(result: ParseResult) -> str:
    """生成解析历史中的平台与文本摘要。"""
    lines = ["[由多平台内容解析插件发送]", f"平台: {result.platform}"]
    if result.title:
        lines.append(f"标题: {result.title}")
    if result.author:
        lines.append(f"作者: {result.author}")
    if result.description:
        lines.append(f"简介:\n{result.description}")
    lines.extend(line for line in result.extra_lines if line)
    if result.error:
        lines.append(result.error)
    return "\n".join(lines)


def _build_media_status(result: ParseResult) -> str:
    """记录独立发送、但暂不写入多模态历史的视频和音频状态。"""
    lines = []
    if result.video_url:
        lines.append("视频: 已发送")
    if result.audio_url:
        lines.append("音频: 已发送")
    return "\n".join(lines)


async def _build_image_part(image_ref: str, image_number: int) -> dict | None:
    """将发送时可用的图片固化为不会随临时文件失效的 data URI。"""
    try:
        data_url = await MediaResolver(
            image_ref,
            media_type="image",
            default_suffix=".bin",
        ).to_data_url(strict=True)
    except Exception as exc:
        logger.warning(
            f"第 {image_number} 张解析图片写入 LLM 会话失败: {type(exc).__name__}"
        )
        return None
    if not data_url:
        return None
    return {
        "type": "image_url",
        "image_url": {"url": data_url},
    }
