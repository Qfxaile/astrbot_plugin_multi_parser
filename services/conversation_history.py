"""将插件解析输出记录到 AstrBot 的 LLM 会话历史。"""

from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

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
    ) -> None:
        """尽力记录成功解析结果，历史写入失败不影响已经完成的消息投递。"""
        try:
            conversation_id = (
                await self.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
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
                    "content": build_parse_history_content(result),
                },
            )
        except Exception as exc:
            logger.warning(
                f"解析结果写入 LLM 会话失败: {type(exc).__name__}"
            )


def build_parse_history_content(result: ParseResult) -> str:
    """生成不含媒体地址、下载请求头和本地路径的解析历史摘要。"""
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

    if result.ordered_contents:
        lines.extend(
            item.value
            for item in result.ordered_contents
            if item.value and item.kind in {"text", "image_error"}
        )
    else:
        lines.extend(error for error in result.image_errors.values() if error)

    image_count = sum(bool(url) for url in result.cover_urls)
    image_count += sum(bool(url) for url in result.image_urls)
    image_count += sum(
        bool(item.value) and item.kind == "image"
        for item in result.ordered_contents
    )
    if image_count:
        lines.append(f"图片: {image_count} 张")
    if result.video_url:
        lines.append("视频: 已发送")
    if result.audio_url:
        lines.append("音频: 已发送")
    return "\n".join(lines)
