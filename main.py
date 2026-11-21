import httpx as httpx
from astrbot.api import AstrBotConfig, logger, web
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
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
from .services.parsing.share_tool import ShareReader
from .services.settings.group_directory import GroupDirectory
from .services.settings.runtime_config import RuntimeSettingsConfig
from .services.settings.service import PluginSettingsService, SettingsValidationError
from .services.summary.service import AISummaryService

__all__ = ["MultiParserPlugin", "VideoSizeInfo"]

PLUGIN_NAME = "astrbot_plugin_multi_parser"


class MultiParserPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._runtime_config = RuntimeSettingsConfig(config)
        self._web_settings = PluginSettingsService(
            config,
            self._runtime_config,
            read_store=self.get_kv_data,
            write_store=self.put_kv_data,
        )
        self.parsers = build_parsers(self._runtime_config)
        self._share_reader = ShareReader(self._access_for_event)
        self._group_directory = GroupDirectory(context)
        self._services = ServiceContainer(context, config)
        if context is not None:
            context.register_web_api(
                f"/{PLUGIN_NAME}/settings",
                self._web_settings_api,
                ["GET", "POST"],
                "读取或保存多平台解析 WebUI 设置（包含明文凭据）",
            )
            context.register_web_api(
                f"/{PLUGIN_NAME}/qq-groups",
                self._web_qq_groups_api,
                ["POST"],
                "读取 QQ 群列表",
            )

    def _delivery_service(self) -> DeliveryService:
        return self._services.delivery

    def _authentication_service(self) -> AuthenticationService:
        return self._services.authentication

    def _conversation_history_service(self) -> ConversationHistoryService:
        return self._services.conversation_history

    def enabled_parsers(self):
        """返回当前启用的平台解析器，供自动解析用例调用。"""
        return enabled_parsers(self.config, self.parsers)

    def _access_for_event(self, event: AstrMessageEvent):
        return self._web_settings.access_rules.for_event(event)

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

    @property
    def tool_mode(self) -> bool:
        return (
            PluginSettings(self.config).choice("parse_mode", {"auto", "tool"}, "auto")
            == "tool"
        )

    @filter.on_llm_request()
    async def offer_share_reader(self, event: AstrMessageEvent, req: ProviderRequest):
        """有分享来源时，为本轮 LLM 提供后台阅读工具。"""
        if self.tool_mode:
            await self._share_reader.offer(event, req, self.enabled_parsers)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def handle_parse(self, event: AstrMessageEvent):
        if self.tool_mode:
            return
        context = extract_context(event)
        if not context.combined_text:
            return
        async for item in ParseCoordinator(self).run(
            event, context, access=self._access_for_event(event)
        ):
            yield item

    async def terminate(self):
        """插件卸载时取消仍在进行的平台登录。"""
        await self._services.authentication.close()

    async def initialize(self) -> None:
        """加载 WebUI 专属解析设置。"""
        await self._web_settings.load()

    async def _web_settings_api(self):
        headers = {"Cache-Control": "no-store"}
        if web.request.method == "GET":
            settings = await self._web_settings.read_snapshot()
            settings["providers"] = self._web_chat_providers()
            settings["adapters"] = self._group_directory.qq_adapters()
            return web.json_response(
                {"status": "ok", "data": settings}, headers=headers
            )
        try:
            payload = await web.request.json()
            settings = await self._web_settings.update(payload)
            return web.json_response(
                {"status": "ok", "data": settings}, headers=headers
            )
        except SettingsValidationError as exc:
            return web.error_response(
                str(exc), status_code=409 if "重新加载" in str(exc) else 400
            )
        except Exception as exc:
            logger.warning(f"WebUI 设置保存失败: {type(exc).__name__}")
            return web.error_response("设置保存失败，请重试。", status_code=500)

    def _web_chat_providers(self) -> list[dict[str, str]]:
        """仅向设置表单公开当前实例已加载聊天模型的 ID 和名称。"""
        providers = []
        try:
            for provider in self.context.get_all_providers():
                try:
                    meta = provider.meta()
                    if isinstance(meta.id, str) and meta.id:
                        model = meta.model if isinstance(meta.model, str) else ""
                        providers.append({"id": meta.id, "model": model})
                except Exception as exc:
                    logger.debug(f"WebUI 读取模型信息失败: {type(exc).__name__}")
        except Exception as exc:
            logger.debug(f"WebUI 读取聊天模型列表失败: {type(exc).__name__}")
        return providers

    async def _web_qq_groups_api(self):
        """返回所选 QQ 实例可用于名单配置的群聊。"""
        payload = await web.request.json()
        instance = payload.get("instance") if isinstance(payload, dict) else None
        if not isinstance(instance, str) or not instance or len(instance) > 128:
            return web.error_response("缺少有效的 QQ 适配器实例 ID", status_code=400)
        try:
            data = await self._group_directory.list_groups(instance)
        except LookupError:
            return web.error_response("找不到所选 QQ 适配器实例", status_code=404)
        except Exception as exc:
            logger.warning(f"读取 QQ 群列表失败: {type(exc).__name__}")
            return web.error_response(
                "无法读取 QQ 群列表，请检查适配器连接。", status_code=502
            )
        return web.json_response(
            {"status": "ok", "data": data}, headers={"Cache-Control": "no-store"}
        )
