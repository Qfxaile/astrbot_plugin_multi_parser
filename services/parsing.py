"""自动链接解析用例，隔离 AstrBot 入口与平台遍历流程。"""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from ..core.contracts import ParseContext, ParseResult
from ..core.http import CookieAccessError
from ..core.parser import Parser
from ..core.settings import PluginSettings


class ParseRuntime(Protocol):
    """解析用例所需的最小运行时能力。"""

    config: Any

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
        self, event: AstrMessageEvent, context: ParseContext
    ) -> AsyncIterator[Any]:
        original_has_send_oper = getattr(event, "_has_send_oper", None)
        for parser in self.runtime.enabled_parsers():
            result: ParseResult | None = None
            restore_send_state = False
            try:
                matched = await parser.match(context)
                if not isinstance(matched, bool):
                    raise TypeError(f"{parser.name} match 必须返回 bool")
                if not matched:
                    continue
                restore_send_state = True
                await self.runtime.react_success(event)
                result = await parser.parse(context)
                if not isinstance(result, ParseResult):
                    raise TypeError(f"{parser.name} parse 必须返回 ParseResult")
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
                if self.runtime.is_forward_delivery(content_results):
                    try:
                        await self.runtime.send_forward_results(
                            event, content_results, result
                        )
                    except Exception as exc:
                        logger.warning(f"{parser.name} 合并转发发送失败: {exc}")
                        yield event.plain_result(
                            f"{parser.name} 合并转发发送失败: {exc}"
                        )
                        if video_embedded:
                            async for fallback in self.runtime.forward_with_fallback(
                                event,
                                result,
                                f"合并转发中的视频发送失败: {type(exc).__name__}",
                            ):
                                yield fallback
                        return
                else:
                    for message in content_results:
                        yield message

                if result.media.audio_url:
                    yield event.chain_result(result.audio_chain())

                if send_video_by_url and result.media.video_url:
                    if should_send_video and not video_embedded:
                        try:
                            await self.runtime.send_video(event, result)
                        except Exception as exc:
                            logger.warning(
                                f"{parser.name} 视频发送失败，执行配置回退: "
                                f"{type(exc).__name__}"
                            )
                            async for fallback in self.runtime.forward_with_fallback(
                                event, result, f"视频发送失败: {type(exc).__name__}"
                            ):
                                yield fallback
                    elif not should_send_video:
                        async for fallback in self.runtime.forward_with_fallback(
                            event, result, video_reason
                        ):
                            yield fallback

                await self.runtime.record_history(event, context.combined_text, result)
                for summary in await self.runtime.summarize(event, result):
                    yield event.plain_result(f"AI总结：\n{summary}")
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
