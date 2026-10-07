"""视频直链回退时的链接摘要和转发路由。"""

from collections.abc import Awaitable, Callable, Mapping

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Node, Nodes, Plain

from ..core.contracts import ParseResult
from .event_identity import EventIdentity
from .onebot_gateway import OneBotGateway


class ForwardLinkDeliveryService:
    """生成视频直链摘要并按平台能力发送。"""

    ONEBOT_PLATFORM = "aiocqhttp"

    def __init__(
        self,
        resolve_identity: Callable[[AstrMessageEvent], Awaitable[tuple[str, str]]],
        supports_forward: Callable[[AstrMessageEvent], bool],
    ):
        self.resolve_identity = resolve_identity
        self.supports_forward = supports_forward

    async def send(
        self, event: AstrMessageEvent, result: ParseResult, reason: str
    ) -> None:
        sender_name, sender_id = await self.resolve_identity(event)
        summary_lines = [
            f"{result.platform} 解析链接",
            f"标题: {result.content.title or '未命名内容'}",
        ]
        if result.content.author:
            summary_lines.append(f"作者: {result.content.author}")
        if reason:
            summary_lines.append(f"说明: {reason}")
        summary_text = "\n".join(summary_lines)
        video_text = f"视频直链:\n{result.media.video_url}"
        platform_name = EventIdentity.platform_name(event)
        if platform_name != self.ONEBOT_PLATFORM and self.supports_forward(event):
            nodes = [
                Node(content=[Plain(text)], name=sender_name, uin=sender_id)
                for text in (summary_text, video_text)
            ]
            await event.send(MessageChain([Nodes(nodes)]))
            return
        if platform_name != self.ONEBOT_PLATFORM:
            await event.send(
                MessageChain(
                    [
                        Plain(
                            "\n".join(
                                [*summary_lines, f"视频链接: {result.media.video_url}"]
                            )
                        )
                    ]
                )
            )
            return

        raw = EventIdentity.raw_message(event)
        raw = raw if isinstance(raw, Mapping) else {}
        nodes = [
            self._raw_node(sender_name, sender_id, summary_text),
            self._raw_node(sender_name, sender_id, video_text),
        ]
        if group_id := raw.get("group_id"):
            await OneBotGateway.call(
                event,
                "send_group_forward_msg",
                group_id=int(group_id),
                messages=nodes,
            )
            return
        await OneBotGateway.call(
            event,
            "send_private_forward_msg",
            user_id=int(raw.get("user_id") or sender_id),
            messages=nodes,
        )

    @staticmethod
    def _raw_node(name: str, user_id: str, text: str) -> dict:
        return {
            "type": "node",
            "data": {
                "name": name,
                "uin": user_id,
                "content": [{"type": "text", "data": {"text": text}}],
            },
        }
