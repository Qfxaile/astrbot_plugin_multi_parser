"""自动链接解析用例，隔离 AstrBot 入口与平台遍历流程。"""

from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any, Protocol

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ...core.contracts import ParseContext, ParseResult
from ...core.http import CookieAccessError
from ...core.media import cleanup_temporary_files
from ...core.parser import Parser
from ...core.rendering import ParseResultRenderer
from ...core.settings import PluginSettings
from ..event_identity import EventIdentity
from ..settings.access_rules import RequestAccess


class ParseRuntime(Protocol):
    """解析用例所需的最小运行时能力。"""

    config: Any

    def record_parse(
        self,
        platform: str,
        status: str,
        duration_ms: int,
        *,
        sent: bool = False,
        error_type: str = "",
        source: str = "",
        umo: str = "",
        group_id: str = "",
        links: str = "",
        result: str = "",
        error_reason: str = "",
    ) -> None: ...

    def enabled_parsers(self) -> list[Parser]: ...

    async def react_success(self, event: AstrMessageEvent) -> None: ...

    async def probe_video_size(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        platform_name: str = "",
    ) -> Any: ...

    def video_send_decision(self, size_info: Any) -> tuple[bool, str]: ...

    def build_content_delivery(
        self,
        event: AstrMessageEvent,
        result: ParseResult,
        *,
        include_video_url: bool,
        include_video: bool,
    ) -> tuple[list, bool]: ...

    async def send_forward_results(
        self, event: AstrMessageEvent, content_results: list, result: ParseResult
    ) -> None: ...

    def is_forward_delivery(self, content_results: list) -> bool: ...

    async def send_video(
        self, event: AstrMessageEvent, result: ParseResult
    ) -> None: ...

    async def forward_with_fallback(
        self, event: AstrMessageEvent, result: ParseResult, reason: str
    ) -> AsyncIterator[Any]: ...

    async def record_history(
        self,
        event: AstrMessageEvent,
        source_text: str,
        result: ParseResult,
    ) -> None: ...

    async def summarize(
        self, event: AstrMessageEvent, result: ParseResult
    ) -> list[str]: ...


class ParseCoordinator:
    """执行一次自动解析，并保持原有事件传播和投递顺序。"""

    def __init__(self, runtime: ParseRuntime):
        self.runtime = runtime

    async def run(
        self, event: AstrMessageEvent, context: ParseContext, *, access: RequestAccess
    ) -> AsyncIterator[Any]:
        original_has_send_oper = getattr(event, "_has_send_oper", None)
        for parser in access.parsers(self.runtime.enabled_parsers()):
            started = perf_counter()
            result: ParseResult | None = None
            restore_send_state = False
            status = ""
            sent = False
            error_type = error_reason = ""
            duration_ms = None
            try:
                with access.feature_scope():
                    matched = await parser.match(context)
                    if not isinstance(matched, bool):
                        raise TypeError(f"{parser.name} match 必须返回 bool")
                    if not matched:
                        continue
                    restore_send_state = True
                    result = await parser.parse(context)
                if not isinstance(result, ParseResult):
                    raise TypeError(f"{parser.name} parse 必须返回 ParseResult")
                if result.diagnostics.skipped:
                    status = "skipped"
                    continue
                send_video_by_url = PluginSettings(self.runtime.config).boolean(
                    "send_video_by_url", True
                )
                should_send_video = False
                video_reason = ""
                if send_video_by_url and result.media.video_url:
                    headers = result.media.video_download_headers or None
                    size_info = await self.runtime.probe_video_size(
                        result.media.video_url,
                        headers,
                        parser.name,
                    )
                    should_send_video, video_reason = self.runtime.video_send_decision(
                        size_info
                    )

                content_results, video_embedded = self.runtime.build_content_delivery(
                    event,
                    result,
                    include_video_url=not send_video_by_url,
                    include_video=should_send_video,
                )
                if not result.diagnostics.error and (
                    content_results or result.media.audio_url or result.media.video_url
                ):
                    await self.runtime.react_success(event)

                if self.runtime.is_forward_delivery(content_results):
                    try:
                        await self.runtime.send_forward_results(
                            event, content_results, result
                        )
                        sent = True
                    except Exception as exc:
                        status = "failure" if result.diagnostics.error else "success"
                        error_type = type(exc).__name__
                        error_reason = result.diagnostics.error or "合并转发发送失败"
                        logger.warning(f"{parser.name} 合并转发发送失败: {exc}")
                        yield event.plain_result(
                            f"{parser.name} 合并转发发送失败: {exc}"
                        )
                        if video_embedded:
                            fallback_failed = False
                            async for fallback in self.runtime.forward_with_fallback(
                                event,
                                result,
                                f"合并转发中的视频发送失败: {type(exc).__name__}",
                            ):
                                fallback_failed = True
                                yield fallback
                            sent = not fallback_failed
                        return
                else:
                    for message in content_results:
                        sent = True
                        yield message

                if result.media.audio_url:
                    sent = True
                    yield event.chain_result(ParseResultRenderer.audio_chain(result))

                fallback_reason = None
                if send_video_by_url and result.media.video_url:
                    if should_send_video and not video_embedded:
                        try:
                            await self.runtime.send_video(event, result)
                            sent = True
                        except Exception as exc:
                            error_type = type(exc).__name__
                            error_reason = "视频发送失败，已执行回退"
                            logger.warning(
                                f"{parser.name} 视频发送失败，执行配置回退: "
                                f"{error_type}"
                            )
                            fallback_reason = f"视频发送失败: {error_type}"
                    elif not should_send_video:
                        fallback_reason = video_reason
                if fallback_reason is not None:
                    fallback_failed = False
                    async for fallback in self.runtime.forward_with_fallback(
                        event, result, fallback_reason
                    ):
                        fallback_failed = True
                        yield fallback
                    if fallback_failed:
                        error_reason = "视频回退发送失败"
                    else:
                        sent = True

                status = "failure" if result.diagnostics.error else "success"
                error_reason = result.diagnostics.error or error_reason
                duration_ms = round((perf_counter() - started) * 1000)

                await self.runtime.record_history(event, context.combined_text, result)
                for summary in await self.runtime.summarize(event, result):
                    yield event.plain_result(f"AI总结：\n{summary}")
                return
            except CookieAccessError as exc:
                restore_send_state = True
                if not status:
                    status, error_type, error_reason = (
                        "failure",
                        type(exc).__name__,
                        str(exc),
                    )
                logger.warning(f"{parser.name} Cookie 访问失败: {exc}")
                yield event.plain_result(str(exc))
                return
            except Exception as exc:
                restore_send_state = True
                if not status:
                    # 任意异常文本可能包含用户输入或请求凭据，不写入持久化日志。
                    status, error_type = "failure", type(exc).__name__
                logger.warning(f"{parser.name} 解析失败: {exc}")
                yield event.plain_result(f"{parser.name} 解析失败: {exc}")
                return
            finally:
                if result is not None:
                    cleanup_temporary_files(result)
                if restore_send_state and original_has_send_oper is not None:
                    event._has_send_oper = original_has_send_oper
                if status:
                    self.runtime.record_parse(
                        parser.name,
                        status,
                        duration_ms
                        if duration_ms is not None
                        else round((perf_counter() - started) * 1000),
                        sent=sent,
                        error_type=error_type,
                        **_parse_log_context(
                            event,
                            context,
                            {
                                "success": "解析成功",
                                "failure": "解析失败",
                                "skipped": "已跳过",
                            }[status],
                            error_reason=error_reason,
                        ),
                    )


def _parse_log_context(
    event: AstrMessageEvent,
    context: ParseContext,
    result: str,
    *,
    error_reason: str = "",
) -> dict[str, str]:
    """只采集定位解析来源所需的会话身份，群 ID 与 AstrBot UMO 分开保存。"""
    adapter = EventIdentity.platform_name(event) or "未知适配器"
    try:
        chat_type = "私聊" if event.is_private_chat() else "群聊"
    except Exception:
        chat_type = "其他会话"
    try:
        umo = str(event.unified_msg_origin or "")
    except Exception:
        umo = ""
    try:
        group_id = str(event.get_group_id() or "") if chat_type == "群聊" else ""
    except Exception:
        group_id = ""
    return {
        "source": f"{adapter} · {chat_type}",
        "umo": umo,
        "group_id": group_id,
        "links": context.combined_text,
        "result": result,
        "error_reason": error_reason,
    }
