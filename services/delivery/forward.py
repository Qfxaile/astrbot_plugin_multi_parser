"""合并转发内容的构建、序列化和发送。"""

from collections.abc import Mapping

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Node, Nodes, Plain

from ...core.contracts import ParseResult
from ...core.rendering import ParseResultRenderer
from ..content_assembly import ContentAssembler
from ..event_identity import EventIdentity
from .onebot_forward import OneBotForwardSerializer
from .onebot_identity import OneBotIdentityResolver
from .onebot_images import OneBotImageDownloader
from .onebot_sender import OneBotForwardSender
from .policy import DeliveryPolicy


class ForwardDeliveryService:
    """负责转发阈值判断、节点构建和协议端发送。"""

    ONEBOT_PLATFORM = "aiocqhttp"
    FORWARD_NODE_PLATFORMS = {"aiocqhttp", "satori"}
    FORWARD_NODE_LIMIT = 100

    def __init__(self, config: Mapping[str, object]) -> None:
        self.policy = DeliveryPolicy(config)
        self.identity = OneBotIdentityResolver()
        self._downloader = OneBotImageDownloader()

    def build(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        info_chain: list,
        *,
        include_video: bool,
    ) -> tuple[list, bool]:
        if not self._supports(event):
            return self._build_non_forward(event, info_chain), False
        if (
            EventIdentity.platform_name(event) == self.ONEBOT_PLATFORM
            and result.delivery.disable_onebot_forward
        ):
            return [event.chain_result(self._merged(event, info_chain))], False
        text_length = sum(
            len(component.text)
            for component in info_chain
            if isinstance(component, Plain)
        )
        if not self.policy.should_forward(result.image_count, text_length):
            return [event.chain_result(self._merged(event, info_chain))], False
        components = list(info_chain)
        embedded = (
            include_video
            and bool(result.media.video_url)
            and (
                self.policy.forward_mode() == "always"
                or result.delivery.keep_video_in_forward
            )
        )
        if embedded:
            components.extend(ParseResultRenderer.video_chain(result))
        sender_name, sender_id = self._cached_identity(event)
        nodes = [
            Node(content=[component], name=sender_name, uin=sender_id)
            for component in ContentAssembler.merge_adjacent_plain(components)
        ]
        return [
            event.chain_result([Nodes(batch)])
            for batch in ContentAssembler.balanced_forward_batches(nodes)
        ], embedded

    def _build_non_forward(self, event: AstrMessageEvent, info_chain: list) -> list:
        if not self._should_limit_non_forward(event):
            return [event.chain_result(info_chain)]
        limit = self.policy.non_forward_message_limit()
        if limit == -1:
            return [event.chain_result(info_chain)]
        messages = ContentAssembler.merge_adjacent_plain(info_chain)
        if len(messages) <= limit:
            return [event.chain_result(info_chain)]
        notice = Plain(
            f"内容共 {len(messages)} 条，超过单次发送上限 {limit} 条；"
            f"以下按原始顺序发送前 {limit} 条。"
        )
        limited_results = [event.chain_result([notice])]
        limited_results.extend(
            event.chain_result([component]) for component in messages[:limit]
        )
        return limited_results

    def _should_limit_non_forward(self, event: AstrMessageEvent) -> bool:
        try:
            is_private = bool(event.is_private_chat())
        except Exception:
            raw = EventIdentity.raw_message(event)
            is_private = (
                not bool(raw.get("group_id")) if isinstance(raw, Mapping) else False
            )
        return not is_private or self.policy.limit_private_chat_messages()

    async def send(
        self,
        event: AstrMessageEvent,
        results: list,
        parse_result: ParseResult,
    ) -> None:
        sender_name, sender_id = await self.identity.resolve(
            event, self._sender_identity(event)
        )
        for result in results:
            chain = getattr(result, "chain", result)
            if len(chain) != 1 or not isinstance(chain[0], Nodes):
                raise ValueError("合并转发结果结构无效")
            nodes = chain[0].nodes
            for node in nodes:
                node.name, node.uin = sender_name, sender_id
            sources = parse_result.media.image_source_urls
            if self._can_use_urls(event, nodes, sources):
                if parse_result.media.image_download_headers:
                    sources = await self._downloader.download(
                        event,
                        nodes,
                        sources,
                        parse_result.media.image_download_headers,
                    )
                await OneBotForwardSender.send(
                    event, await OneBotForwardSerializer.serialize(nodes, sources)
                )
            else:
                await event.send(MessageChain([Nodes(nodes)]))

    def _cached_identity(self, event: AstrMessageEvent) -> tuple[str, str]:
        return self.identity.cached(event) or self._sender_identity(event)

    @staticmethod
    def _sender_identity(event: AstrMessageEvent) -> tuple[str, str]:
        return EventIdentity.sender_identity(event)

    @classmethod
    def _supports(cls, event: AstrMessageEvent) -> bool:
        return EventIdentity.platform_name(event) in cls.FORWARD_NODE_PLATFORMS

    @classmethod
    def _merged(cls, event: AstrMessageEvent, components: list) -> list:
        if EventIdentity.platform_name(event) == cls.ONEBOT_PLATFORM:
            return ContentAssembler.merge_adjacent_plain(components)
        return components

    @classmethod
    def _can_use_urls(
        cls, event: AstrMessageEvent, nodes: list[Node], sources: Mapping[str, str]
    ) -> bool:
        if EventIdentity.platform_name(event) != cls.ONEBOT_PLATFORM:
            return False
        images = [
            component
            for node in nodes
            for component in node.content
            if isinstance(component, Image)
        ]
        return bool(images) and all(
            OneBotForwardSerializer.remote_image_url(image, sources) for image in images
        )
