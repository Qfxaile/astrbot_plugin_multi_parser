from collections.abc import Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import Nodes

from ..core.contracts import ParseResult
from ..core.settings import PluginSettings
from .delivery_policy import DeliveryPolicy
from .event_identity import EventIdentity
from .forward_delivery import ForwardDeliveryService
from .forward_link_delivery import ForwardLinkDeliveryService
from .link_filter import LinkFilter
from .onebot_gateway import OneBotGateway
from .video_delivery import VideoDeliveryService
from .video_fallback import VideoFallbackService


class DeliveryService:
    """封装 AstrBot 跨平台消息组件编排与平台特有增强。"""

    ONEBOT_PLATFORM = "aiocqhttp"
    KOOK_PLATFORM = "kook"
    FORWARD_NODE_PLATFORMS = {"aiocqhttp", "satori"}
    FORWARD_MODES = {"always", "threshold", "never"}
    DEFAULT_FORWARD_MODE = "threshold"
    DEFAULT_IMAGE_THRESHOLD = 2
    DEFAULT_TEXT_THRESHOLD = 200
    FORWARD_NODE_LIMIT = 100
    VIDEO_OVER_LIMIT_ACTIONS = {"notice", "direct_link", "group_file"}
    DEFAULT_VIDEO_OVER_LIMIT_ACTION = "direct_link"
    DEFAULT_FILTERED_LINK_TEXT = "[详细内容请打开原链接查看]"

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = config
        self.policy = DeliveryPolicy(config)
        self.settings = PluginSettings(config)
        self.video_delivery = VideoDeliveryService(config)
        self.video_fallback = VideoFallbackService(config, self.send_forward_links)
        self.link_filter = LinkFilter(config)
        self.forward_delivery = ForwardDeliveryService(config)
        self.forward_link_delivery = ForwardLinkDeliveryService(
            lambda event: self.forward_delivery.identity.resolve(
                event, self.sender_identity(event, prefer_raw_nickname=True)
            ),
            self._supports_forward_nodes,
        )

    @staticmethod
    async def call_onebot(event: AstrMessageEvent, action: str, **params):
        return await OneBotGateway.call(event, action, **params)

    @staticmethod
    def raw_message(event: AstrMessageEvent):
        return EventIdentity.raw_message(event)

    def message_id(self, event: AstrMessageEvent) -> str:
        return EventIdentity.message_id(event)

    async def react_success(self, event: AstrMessageEvent) -> None:
        if not self.settings.boolean("enable_parse_reaction", True):
            return
        if self._platform_name(event) != self.ONEBOT_PLATFORM:
            return

        message_id = self.message_id(event)
        if not message_id:
            logger.info("解析成功表情回应失败: 未获取到 message_id")
            return

        action = self.settings.text("reaction_action", "set_msg_emoji_like")
        emoji_id = self.settings.text("reaction_emoji_id", "124")
        if not action or not emoji_id:
            return

        try:
            await self.call_onebot(
                event, action, message_id=int(message_id), emoji_id=emoji_id
            )
        except Exception as exc:
            logger.info(f"解析成功表情回应失败: {exc}")

    def build_content_results(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        *,
        include_video_url: bool,
    ) -> list:
        results, _ = self.build_content_delivery(
            event,
            result,
            include_video_url=include_video_url,
        )
        return results

    async def send_video(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
    ) -> None:
        """准备并发送视频，让调用方可以捕获协议端发送失败。"""
        await self.video_delivery.send(event, result)

    def build_content_delivery(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        *,
        include_video_url: bool,
        include_video: bool = False,
    ) -> tuple[list, bool]:
        info_chain = self._filter_output_links(
            result.info_chain(include_video_url=include_video_url)
        )
        if not info_chain:
            return [], False
        if self._should_split_onebot_content(event, result):
            return [event.chain_result([component]) for component in info_chain], False
        return self.forward_delivery.build(
            event, result, info_chain, include_video=include_video
        )

    async def send_forward_results(
        self,
        event: AstrMessageEvent,
        results: list,
        parse_result: ParseResult,
    ) -> None:
        """发送合并转发，只有构建阶段超过节点上限时才会分批。"""
        await self.forward_delivery.send(event, results, parse_result)

    @staticmethod
    def is_forward_delivery(results: list) -> bool:
        if not results:
            return False
        chain = getattr(results[0], "chain", results[0])
        return len(chain) == 1 and isinstance(chain[0], Nodes)

    @classmethod
    def _should_split_onebot_content(
        cls,
        event: AstrMessageEvent,
        result: ParseResult,
    ) -> bool:
        return (
            cls._platform_name(event) == cls.ONEBOT_PLATFORM
            and result.split_media_for_onebot
        )

    def _forward_mode(self) -> str:
        return self.policy.forward_mode()

    async def send_forward_links(
        self, event: AstrMessageEvent, result: ParseResult, reason: str
    ) -> None:
        """按适配器能力发送视频链接，非转发平台降级为普通文本。"""
        await self.forward_link_delivery.send(event, result, reason)

    async def send_video_over_limit(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        reason: str,
    ) -> None:
        """按配置回退未直接送达的视频，群文件失败时降级为直链。"""
        await self.video_fallback.send(event, result, reason)

    def video_over_limit_action(self) -> str:
        """读取视频回退处理方式，无效值按发送直链处理。"""
        return self.policy.video_over_limit_action()

    def _filter_output_links(self, components: list) -> list:
        """仅过滤插件生成的可见文本，不改写媒体组件和主动发送的直链。"""
        return self.link_filter.apply(components)

    def forward_node_identity(
        self,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        """QQ 合并转发先使用已缓存名称或账号，其他平台沿用发送者身份。"""
        return self.forward_delivery.identity.cached(event) or self.sender_identity(
            event, prefer_raw_nickname=prefer_raw_nickname
        )

    async def resolve_forward_node_identity(
        self,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        """发送前通过 OneBot 登录信息解析 QQ 昵称，并缓存到当前服务实例。"""
        return await self.forward_delivery.identity.resolve(
            event,
            self.sender_identity(event, prefer_raw_nickname=prefer_raw_nickname),
        )

    def sender_identity(
        self,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        return EventIdentity.sender_identity(
            event, prefer_raw_nickname=prefer_raw_nickname
        )

    @classmethod
    def _supports_forward_nodes(cls, event: AstrMessageEvent) -> bool:
        return cls._platform_name(event) in cls.FORWARD_NODE_PLATFORMS

    @staticmethod
    def _platform_name(event: AstrMessageEvent) -> str:
        return EventIdentity.platform_name(event)

    @staticmethod
    def _raw_forward_node(name: str, user_id: str, text: str) -> dict:
        return {
            "type": "node",
            "data": {
                "name": name,
                "uin": user_id,
                "content": [{"type": "text", "data": {"text": text}}],
            },
        }
