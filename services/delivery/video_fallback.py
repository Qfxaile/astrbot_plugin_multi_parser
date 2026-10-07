"""视频无法直接发送时的回退策略。"""

import re
from collections.abc import Awaitable, Callable, Mapping
from pathlib import PurePosixPath
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain

from ...core.contracts import ParseResult
from ..event_identity import EventIdentity
from .onebot_gateway import OneBotGateway
from .policy import DeliveryPolicy


class VideoFallbackService:
    """执行提示、群文件和直链回退，不负责普通视频发送。"""

    ONEBOT_PLATFORM = "aiocqhttp"

    def __init__(
        self,
        config: Mapping[str, object],
        send_forward_links: Callable[
            [AstrMessageEvent, ParseResult, str], Awaitable[None]
        ],
    ):
        self.policy = DeliveryPolicy(config)
        self.send_forward_links = send_forward_links

    async def send(
        self, event: AstrMessageEvent, result: ParseResult, reason: str
    ) -> None:
        action = self.policy.video_over_limit_action()
        if action == "notice":
            await event.send(MessageChain([Plain(reason or "视频未直接发送。")]))
            return

        group_id = self._group_id(event)
        if action == "group_file" and group_id:
            try:
                await OneBotGateway.call(
                    event,
                    "upload_group_file",
                    group_id=group_id,
                    file=result.media.video_url,
                    name=self.file_name(result),
                )
                return
            except Exception as exc:
                logger.warning(
                    f"视频群文件发送失败，已降级为直链: {type(exc).__name__}"
                )

        await self.send_forward_links(event, result, reason)

    @classmethod
    def _group_id(cls, event: AstrMessageEvent) -> int | None:
        if EventIdentity.platform_name(event) != cls.ONEBOT_PLATFORM:
            return None
        raw = EventIdentity.raw_message(event)
        raw_group_id = raw.get("group_id") if isinstance(raw, dict) else None
        try:
            group_id = raw_group_id or event.get_group_id()
            return int(group_id) if group_id else None
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def file_name(result: ParseResult) -> str:
        base_name = (result.content.title or f"{result.platform}视频").strip()
        base_name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base_name)
        base_name = base_name.strip(" ._")[:80] or "video"
        suffix = PurePosixPath(urlparse(result.media.video_url).path).suffix.lower()
        if suffix not in {".mp4", ".mov", ".mkv", ".webm", ".flv", ".avi"}:
            suffix = ".mp4"
        return f"{base_name}{suffix}"
