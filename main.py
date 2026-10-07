import httpx as httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.contracts import ParseResult
from .core.settings import PluginSettings
from .services.ai_summary import AISummaryService
from .services.authentication import AuthenticationService
from .services.configuration import build_parsers, enabled_parsers
from .services.container import ServiceContainer
from .services.conversation_history import ConversationHistoryService
from .services.delivery import DeliveryService
from .services.message_context import extract_context
from .services.parsing import ParseCoordinator
from .services.video import (
    VideoSendPolicy,
    VideoSizeInfo,
    VideoSizeProbe,
    format_video_size,
    parse_content_range,
)

__all__ = ["MultiParserPlugin", "VideoSizeInfo"]


class MultiParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.parsers = build_parsers(config)
        self._services = ServiceContainer(context, config)

    def _delivery_service(self) -> DeliveryService:
        services = getattr(self, "_services", None)
        if services is not None:
            return services.delivery
        delivery = getattr(self, "_delivery", None)
        if delivery is None:
            delivery = DeliveryService(self.config)
            self._delivery = delivery
        return delivery

    def _authentication_service(self) -> AuthenticationService:
        services = getattr(self, "_services", None)
        if services is not None:
            return services.authentication
        authentication = getattr(self, "_authentication", None)
        if authentication is None:
            authentication = AuthenticationService(self.config)
            self._authentication = authentication
        return authentication

    def _conversation_history_service(self) -> ConversationHistoryService:
        services = getattr(self, "_services", None)
        if services is not None:
            return services.conversation_history
        history = getattr(self, "_conversation_history", None)
        if history is None:
            history = ConversationHistoryService(self.context.conversation_manager)
            self._conversation_history = history
        return history

    def _enabled_parsers(self):
        return enabled_parsers(self.config, self.parsers)

    def enabled_parsers(self):
        """返回当前启用的平台解析器，供自动解析用例调用。"""
        return self._enabled_parsers()

    # ParseCoordinator 只依赖这些窄接口；保留方法名便于平台和测试替换单项能力。
    async def react_success(self, event: AstrMessageEvent) -> None:
        await self._react_success(event)

    async def probe_video_size(self, url, headers=None, platform_name=""):
        return await self._probe_video_size(url, headers, platform_name)

    def video_send_decision(self, size_info):
        return self._video_send_decision(size_info)

    def build_content_delivery(
        self, event, result, *, include_video_url, include_video
    ):
        return self._delivery_service().build_content_delivery(
            event,
            result,
            include_video_url=include_video_url,
            include_video=include_video,
        )

    async def send_forward_results(self, event, content_results, result):
        await self._delivery_service().send_forward_results(
            event, content_results, result
        )

    def is_forward_delivery(self, content_results):
        return self._delivery_service().is_forward_delivery(content_results)

    async def send_video(self, event, result):
        await self._delivery_service().send_video(event, result)

    async def forward_with_fallback(self, event, result, reason):
        async for item in self._forward_with_fallback(event, result, reason):
            yield item

    async def record_history(self, event, source_text, result):
        settings = PluginSettings(self.config)
        if not settings.boolean("enable_conversation_history"):
            return
        history_mode = settings.choice(
            "conversation_history_mode", {"text_only", "text_and_images"}, "text_only"
        )
        await self._conversation_history_service().record_parse_result(
            event,
            source_text,
            result,
            include_images=history_mode == "text_and_images",
        )

    async def summarize(self, event, result):
        return await self._ai_summary_service().summarize(event, result)

    def _ai_summary_service(self) -> AISummaryService:
        services = getattr(self, "_services", None)
        if services is not None:
            return services.ai_summary
        service = getattr(self, "_ai_summary", None)
        if service is None:
            service = AISummaryService(self.context, self.config)
            self._ai_summary = service
        return service

    @staticmethod
    async def _call_onebot(event: AstrMessageEvent, action: str, **params):
        return await DeliveryService.call_onebot(event, action, **params)

    @staticmethod
    def _raw(event: AstrMessageEvent):
        return DeliveryService.raw_message(event)

    def _message_id(self, event: AstrMessageEvent) -> str:
        return self._delivery_service().message_id(event)

    async def _react_success(self, event: AstrMessageEvent) -> None:
        await self._delivery_service().react_success(event)

    @staticmethod
    def _format_size(size_mb: float | None) -> str:
        return format_video_size(size_mb)

    @staticmethod
    def _parse_content_range(value: str) -> int | None:
        return parse_content_range(value)

    async def _probe_video_size(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        platform_name: str = "",
    ) -> VideoSizeInfo:
        return await VideoSizeProbe(self.config, platform_name).probe(url, headers)

    def _video_send_decision(self, size_info: VideoSizeInfo) -> tuple[bool, str]:
        return VideoSendPolicy(self.config).decide(size_info)

    async def _send_forward_links(
        self, event: AstrMessageEvent, result: ParseResult, reason: str
    ) -> None:
        await self._delivery_service().send_forward_links(event, result, reason)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("平台登录")
    async def platform_login(
        self,
        event: AstrMessageEvent,
        platform_name: str = "",
    ):
        """在管理员私聊中登录指定中文名平台。"""
        if not event.is_private_chat():
            yield event.plain_result("平台登录仅允许管理员在私聊中操作。")
            return
        message = await self._authentication_service().login(event, platform_name)
        if message:
            yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("平台登录状态")
    async def platform_login_status(self, event: AstrMessageEvent):
        """查看平台登录配置状态与当前账号。"""
        yield event.plain_result(await self._authentication_service().status())

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("平台退出")
    async def platform_logout(
        self,
        event: AstrMessageEvent,
        platform_name: str = "",
    ):
        """在管理员私聊中清除指定平台登录态。"""
        if not event.is_private_chat():
            yield event.plain_result("平台退出仅允许管理员在私聊中操作。")
            return
        message = await self._authentication_service().logout(platform_name)
        yield event.plain_result(message)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("取消平台登录")
    async def cancel_platform_login(self, event: AstrMessageEvent):
        """取消当前管理员私聊发起的平台登录。"""
        if not event.is_private_chat():
            yield event.plain_result("取消平台登录仅允许管理员在私聊中操作。")
            return
        message = await self._authentication_service().cancel(event)
        yield event.plain_result(message)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_parse(self, event: AstrMessageEvent):
        context = extract_context(event)
        if not context.combined_text:
            return
        async for item in ParseCoordinator(self).run(event, context):
            yield item

    async def _forward_with_fallback(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        reason: str,
    ):
        try:
            await self._delivery_service().send_video_over_limit(
                event,
                result,
                reason,
            )
        except Exception as exc:
            logger.warning(f"视频超限处理失败: {exc}")
            message = f"{reason}\n视频超限处理失败: {exc}"
            if self._delivery_service().video_over_limit_action() != "notice":
                message = f"{message}\n视频链接: {result.media.video_url}"
            yield event.plain_result(message)

    async def terminate(self):
        """插件卸载时取消仍在进行的平台登录。"""
        services = getattr(self, "_services", None)
        authentication = (
            services.authentication
            if services is not None
            else getattr(self, "_authentication", None)
        )
        if authentication is not None:
            await authentication.close()
