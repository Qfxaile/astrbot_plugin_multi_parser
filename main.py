import httpx as httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.settings import PluginSettings
from .services.authentication.service import AuthenticationService
from .services.composition.configuration import build_parsers, enabled_parsers
from .services.composition.container import ServiceContainer
from .services.conversation.history import ConversationHistoryService
from .services.delivery.service import DeliveryService
from .services.delivery.video import (
    VideoSendPolicy,
    VideoSizeInfo,
    VideoSizeProbe,
    format_video_size,
    parse_content_range,
)
from .services.message_context import extract_context
from .services.parsing import ParseCoordinator
from .services.summary.service import AISummaryService

__all__ = ["MultiParserPlugin", "VideoSizeInfo"]


class MultiParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.parsers = build_parsers(config)
        self._services = ServiceContainer(context, config)

    def _delivery_service(self) -> DeliveryService:
        return self._services.delivery

    def _authentication_service(self) -> AuthenticationService:
        return self._services.authentication

    def _conversation_history_service(self) -> ConversationHistoryService:
        return self._services.conversation_history

    def enabled_parsers(self):
        """返回当前启用的平台解析器，供自动解析用例调用。"""
        return enabled_parsers(self.config, self.parsers)

    # ParseCoordinator 依赖的窄接口，直接提供实现
    async def react_success(self, event: AstrMessageEvent) -> None:
        await self._delivery_service().react_success(event)

    async def probe_video_size(self, url, headers=None, platform_name=""):
        return await VideoSizeProbe(self.config, platform_name).probe(url, headers)

    def video_send_decision(self, size_info):
        return VideoSendPolicy(self.config).decide(size_info)

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
        return self._services.ai_summary

    @staticmethod
    async def _call_onebot(event: AstrMessageEvent, action: str, **params):
        return await DeliveryService.call_onebot(event, action, **params)

    @staticmethod
    def _raw(event: AstrMessageEvent):
        return DeliveryService.raw_message(event)

    @staticmethod
    def _format_size(size_mb: float | None) -> str:
        return format_video_size(size_mb)

    @staticmethod
    def _parse_content_range(value: str) -> int | None:
        return parse_content_range(value)

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

    async def terminate(self):
        """插件卸载时取消仍在进行的平台登录。"""
        await self._services.authentication.close()
