"""视频消息准备和发送。"""

from collections.abc import Mapping
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Video

from ...core.contracts import ParseResult
from ...core.media import TemporaryFileRegistry, VideoMaterializer
from ...core.rendering import ParseResultRenderer


class VideoDeliveryService:
    """负责视频组件准备、平台特例物化和发送。"""

    KOOK_PLATFORM = "kook"

    def __init__(self, config: Mapping[str, object]):
        self.config = config

    async def send(self, event: AstrMessageEvent, result: ParseResult) -> None:
        video_chain = ParseResultRenderer.video_chain(result)
        if self._platform_name(event) == self.KOOK_PLATFORM and video_chain:
            if result.media.video_download_host_suffixes:
                video_path = await VideoMaterializer(
                    self.config,
                    result.media.video_download_host_suffixes,
                ).materialize(result)
            else:
                video_path = Path(await video_chain[0].convert_to_file_path()).resolve()
                TemporaryFileRegistry.register(result, video_path)
            video_chain = [Video.fromFileSystem(video_path)]
        await event.send(MessageChain(video_chain))

    @staticmethod
    def _platform_name(event: AstrMessageEvent) -> str:
        try:
            return str(event.get_platform_name() or "")
        except Exception:
            return ""
