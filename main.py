import httpx as httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .core.contracts import ParseResult
from .core.http import CookieAccessError
from .services.authentication import AuthenticationService
from .services.configuration import build_parsers, enabled_parsers
from .services.conversation_history import ConversationHistoryService
from .services.delivery import DeliveryService
from .services.message_context import extract_context
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
        self._authentication = AuthenticationService(config)
        self._delivery = DeliveryService(config)

    def _delivery_service(self) -> DeliveryService:
        delivery = getattr(self, "_delivery", None)
        if delivery is None:
            delivery = DeliveryService(self.config)
            self._delivery = delivery
        return delivery

    def _authentication_service(self) -> AuthenticationService:
        authentication = getattr(self, "_authentication", None)
        if authentication is None:
            authentication = AuthenticationService(self.config)
            self._authentication = authentication
        return authentication

    def _conversation_history_service(self) -> ConversationHistoryService:
        history = getattr(self, "_conversation_history", None)
        if history is None:
            history = ConversationHistoryService(self.context.conversation_manager)
            self._conversation_history = history
        return history

    def _enabled_parsers(self):
        return enabled_parsers(self.config, self.parsers)

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
    ) -> VideoSizeInfo:
        return await VideoSizeProbe(self.config).probe(url, headers)

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
        original_has_send_oper = getattr(event, "_has_send_oper", None)
        context = extract_context(event)
        if not context.combined_text:
            return

        for parser in self._enabled_parsers():
            result: ParseResult | None = None
            restore_send_state = False
            try:
                if not await parser.match(context):
                    continue
                restore_send_state = True
                await self._react_success(event)
                result = await parser.parse(context)
                send_video_by_url = bool(self.config.get("send_video_by_url", True))
                should_send_video = False
                video_reason = ""
                if send_video_by_url and result.video_url:
                    if result.video_download_headers:
                        video_size_info = await self._probe_video_size(
                            result.video_url,
                            result.video_download_headers,
                        )
                    else:
                        video_size_info = await self._probe_video_size(result.video_url)
                    should_send_video, video_reason = self._video_send_decision(
                        video_size_info
                    )

                content_results, video_embedded = (
                    self._delivery_service().build_content_delivery(
                        event,
                        result,
                        include_video_url=not send_video_by_url,
                        include_video=should_send_video,
                    )
                )
                delivery = self._delivery_service()
                if delivery.is_forward_delivery(content_results):
                    try:
                        await delivery.send_forward_results(
                            event, content_results, result
                        )
                    except Exception as exc:
                        logger.warning(f"{parser.name} 合并转发发送失败: {exc}")
                        yield event.plain_result(
                            f"{parser.name} 合并转发发送失败: {exc}"
                        )
                        if video_embedded:
                            async for fallback in self._forward_with_fallback(
                                event,
                                result,
                                f"合并转发中的视频发送失败: {type(exc).__name__}",
                            ):
                                yield fallback
                        return
                else:
                    for message in content_results:
                        yield message

                if result.audio_url:
                    yield event.chain_result(result.audio_chain())

                if send_video_by_url and result.video_url:
                    if should_send_video and not video_embedded:
                        try:
                            await delivery.send_video(event, result)
                        except Exception as exc:
                            logger.warning(
                                f"{parser.name} 视频发送失败，执行配置回退: "
                                f"{type(exc).__name__}"
                            )
                            async for fallback in self._forward_with_fallback(
                                event,
                                result,
                                f"视频发送失败: {type(exc).__name__}",
                            ):
                                yield fallback
                    elif not should_send_video:
                        async for fallback in self._forward_with_fallback(
                            event,
                            result,
                            video_reason,
                        ):
                            yield fallback
                if bool(self.config.get("enable_conversation_history", False)):
                    history_mode = str(
                        self.config.get("conversation_history_mode", "text_only")
                    ).strip()
                    await self._conversation_history_service().record_parse_result(
                        event,
                        context.combined_text,
                        result,
                        include_images=history_mode == "text_and_images",
                    )
                return
            except CookieAccessError as exc:
                restore_send_state = True
                logger.warning(f"{parser.name} Cookie 访问失败: {exc}")
                yield event.plain_result(str(exc))
                return
            except Exception as exc:
                restore_send_state = True
                logger.warning(f"{parser.name} 解析失败: {exc}")
                yield event.plain_result(f"{parser.name} 解析失败: {exc}")
                return
            finally:
                if result is not None:
                    result.cleanup_temporary_files()
                if restore_send_state and original_has_send_oper is not None:
                    event._has_send_oper = original_has_send_oper

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
                message = f"{message}\n视频链接: {result.video_url}"
            yield event.plain_result(message)

    async def terminate(self):
        """插件卸载时取消仍在进行的平台登录。"""
        authentication = getattr(self, "_authentication", None)
        if authentication is not None:
            await authentication.close()
