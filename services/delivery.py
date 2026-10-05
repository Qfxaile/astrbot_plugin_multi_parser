from collections.abc import Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Node, Nodes, Plain

from ..core.contracts import ParseResult
from ..core.settings import PluginSettings
from .content_assembly import ContentAssembler
from .delivery_policy import DeliveryPolicy
from .event_identity import EventIdentity
from .forward_link_delivery import ForwardLinkDeliveryService
from .link_filter import LinkFilter
from .onebot_forward import OneBotForwardSerializer
from .onebot_forward_sender import OneBotForwardSender
from .onebot_gateway import OneBotGateway
from .onebot_image_downloader import OneBotImageDownloader
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
    _image_downloader = OneBotImageDownloader()

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = config
        self.policy = DeliveryPolicy(config)
        self.settings = PluginSettings(config)
        self.video_delivery = VideoDeliveryService(config)
        self.video_fallback = VideoFallbackService(config, self.send_forward_links)
        self.link_filter = LinkFilter(config)
        self.forward_link_delivery = ForwardLinkDeliveryService(
            lambda event: self.resolve_forward_node_identity(
                event, prefer_raw_nickname=True
            ),
            self._supports_forward_nodes,
        )
        self._onebot_names: dict[str, str] = {}

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
        if not self._should_forward_content(event, result, info_chain):
            if self._platform_name(event) == self.ONEBOT_PLATFORM:
                info_chain = self._merge_adjacent_plain_components(info_chain)
            return [event.chain_result(info_chain)], False

        forward_components = list(info_chain)
        video_embedded = (
            include_video
            and bool(result.video_url)
            and (self._forward_mode() == "always" or result.keep_video_in_forward)
        )
        if video_embedded:
            forward_components.extend(result.video_chain())
        sender_name, sender_id = self.forward_node_identity(event)
        merged_components = self._merge_adjacent_plain_components(forward_components)
        nodes = [
            Node(content=[component], name=sender_name, uin=sender_id)
            for component in merged_components
        ]
        results = [
            event.chain_result([Nodes(batch)])
            for batch in self._balanced_forward_batches(nodes)
        ]
        return results, video_embedded

    @classmethod
    def _balanced_forward_batches(cls, nodes: list[Node]) -> list[list[Node]]:
        """均衡拆分超长转发，避免首批贴近上限而尾批过小。"""
        return ContentAssembler.balanced_forward_batches(nodes)

    async def send_forward_results(
        self,
        event: AstrMessageEvent,
        results: list,
        parse_result: ParseResult,
    ) -> None:
        """发送合并转发，只有构建阶段超过节点上限时才会分批。"""
        sender_name, sender_id = await self.resolve_forward_node_identity(event)
        for result in results:
            chain = getattr(result, "chain", result)
            if len(chain) != 1 or not isinstance(chain[0], Nodes):
                raise ValueError("合并转发结果结构无效")
            nodes = chain[0].nodes
            for node in nodes:
                node.name = sender_name
                node.uin = sender_id
            if self._can_send_onebot_url_forward(
                event, nodes, parse_result.media_metadata.image_source_urls
            ):
                image_files = parse_result.media_metadata.image_source_urls
                if parse_result.media_metadata.image_download_headers:
                    image_files = await self._download_onebot_forward_images(
                        event,
                        nodes,
                        parse_result.media_metadata.image_source_urls,
                        parse_result.media_metadata.image_download_headers,
                    )
                messages = await self._serialize_onebot_nodes(nodes, image_files)
                await self._send_onebot_forward_nodes(event, messages)
                continue
            await event.send(MessageChain([Nodes(nodes)]))

    @classmethod
    def _can_send_onebot_url_forward(
        cls,
        event: AstrMessageEvent,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
    ) -> bool:
        """仅在 aiocqhttp 的全部图片都有远程地址时绕过 Base64 序列化。"""
        if cls._platform_name(event) != cls.ONEBOT_PLATFORM:
            return False
        images = [
            component
            for node in nodes
            for component in node.content
            if isinstance(component, Image)
        ]
        return bool(images) and all(
            cls._remote_image_url(image, image_source_urls) for image in images
        )

    @classmethod
    async def _serialize_onebot_nodes(
        cls,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
    ) -> list[dict]:
        """构造使用远程图片 URL 的 OneBot 节点，避免 WebSocket 携带 Base64。"""
        return await OneBotForwardSerializer.serialize(nodes, image_source_urls)

    @classmethod
    async def _download_onebot_forward_images(
        cls,
        event: AstrMessageEvent,
        nodes: list[Node],
        image_source_urls: Mapping[str, str],
        headers: Mapping[str, str],
    ) -> dict[str, str]:
        """兼容入口，委托给独立图片预下载服务。"""
        return await cls._image_downloader.download(
            event, nodes, image_source_urls, headers
        )

    @staticmethod
    def _remote_image_url(image: Image, image_source_urls: Mapping[str, str]) -> str:
        return OneBotForwardSerializer.remote_image_url(image, image_source_urls)

    async def _send_onebot_forward_nodes(
        self, event: AstrMessageEvent, messages: list[dict]
    ) -> None:
        """将已序列化的 URL 节点直接交给 OneBot，避免 AstrBot 转为 Base64。"""
        await OneBotForwardSender.send(event, messages)

    @staticmethod
    def is_forward_delivery(results: list) -> bool:
        if not results:
            return False
        chain = getattr(results[0], "chain", results[0])
        return len(chain) == 1 and isinstance(chain[0], Nodes)

    @classmethod
    def _merge_adjacent_plain_components(cls, components: list) -> list:
        """合并相邻文本并保留媒体边界与原始顺序。"""
        return ContentAssembler.merge_adjacent_plain(components)

    @staticmethod
    def _join_plain_text(previous: str, current: str) -> str:
        return ContentAssembler.join_plain_text(previous, current)

    def _should_forward_content(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        chain: list,
    ) -> bool:
        if not self._supports_forward_nodes(event):
            return False
        if (
            self._platform_name(event) == self.ONEBOT_PLATFORM
            and result.disable_onebot_forward
        ):
            return False

        text_length = sum(
            len(component.text) for component in chain if isinstance(component, Plain)
        )
        return self.policy.should_forward(result.image_count, text_length)

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
        if self._platform_name(event) == self.ONEBOT_PLATFORM:
            try:
                bot_id = str(event.get_self_id() or "")
            except Exception:
                bot_id = ""

            if bot_id:
                return self._onebot_names.get(bot_id, bot_id), bot_id

        return self.sender_identity(
            event,
            prefer_raw_nickname=prefer_raw_nickname,
        )

    async def resolve_forward_node_identity(
        self,
        event: AstrMessageEvent,
        *,
        prefer_raw_nickname: bool = False,
    ) -> tuple[str, str]:
        """发送前通过 OneBot 登录信息解析 QQ 昵称，并缓存到当前服务实例。"""
        sender_name, sender_id = self.forward_node_identity(
            event,
            prefer_raw_nickname=prefer_raw_nickname,
        )
        if self._platform_name(event) != self.ONEBOT_PLATFORM:
            return sender_name, sender_id
        try:
            bot_id = str(event.get_self_id() or "")
        except Exception:
            bot_id = ""
        if not bot_id:
            return sender_name, sender_id
        if bot_id in self._onebot_names:
            return self._onebot_names[bot_id], bot_id

        try:
            login_info = await self.call_onebot(
                event,
                "get_login_info",
                self_id=int(bot_id),
            )
            if isinstance(login_info, Mapping):
                bot_name = str(login_info.get("nickname") or "").strip()
                if bot_name:
                    self._onebot_names[bot_id] = bot_name
                    return bot_name, bot_id
        except Exception as exc:
            logger.info(f"获取 QQ 机器人名称失败: {type(exc).__name__}")

        return sender_name, bot_id

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
