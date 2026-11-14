"""为当前 LLM 请求提供分享阅读工具，只有实际调用才抓取内容。"""

import re
from collections import OrderedDict
from dataclasses import replace
from functools import partial

from astrbot.api import FunctionTool, ToolSet, logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.core.utils.media_utils import MediaResolver
from mcp.types import CallToolResult, ImageContent, TextContent

from ...core.contracts import ParseContext, ParseResult
from ...core.http import CookieAccessError
from ...core.media import TemporaryFileRegistry, cleanup_temporary_files
from ...core.media.operations import requested_image_number
from ...core.parser import Parser
from ..delivery.onebot_gateway import OneBotGateway
from ..message_context import context_from_segments, extract_context, quoted_message


class ShareReader:
    def __init__(self):
        # 只记成功打开的来源；按 AstrBot 会话分开，新会话与插件重载均从空开始。
        self.recent: OrderedDict[tuple[str, str], ParseContext] = OrderedDict()

    async def offer(
        self, event: AstrMessageEvent, req: ProviderRequest, parsers: list[Parser]
    ):
        """提供受当前消息、引用或本会话最近阅读来源约束的工具。"""
        source = extract_context(event)
        reply = quoted_message(event)
        key = (
            (event.unified_msg_origin, req.conversation.cid)
            if req.conversation
            else None
        )
        # 纯贴链（包括卡片）即使触发其他插件的 LLM 请求，也不提供抓取入口。
        text = event.message_str.strip()
        if text.startswith(("https://", "http://")):
            # 中文标点可直接分隔链接和提问；短链后也常直接跟中文问句。
            adjacent = re.match(
                r"(https?://[^\s，。！？；：、]+)[，。！？；：、]\s*\S", text
            )
            if adjacent is None:
                adjacent = re.fullmatch(r"(https?://[!-~]+)[\u3400-\u9fff]+[？?]", text)
            if adjacent:
                source = replace(source, text=adjacent[1])
            elif not reply and not re.search(r"\s", text):
                return
        if not reply and source.json_urls and not text:
            return
        parser = await _matching_parser(parsers, source)
        pending_reply = None
        if parser is None and reply:
            source = context_from_segments(
                reply.chain or [], reply.message_str or reply.text or ""
            )
            parser = await _matching_parser(parsers, source)
            if not source.combined_text and event.get_platform_name() == "aiocqhttp":
                pending_reply = reply.id
        elif (
            parser is None and key in self.recent and "://" not in source.combined_text
        ):
            source = self.recent[key]
            parser = await _matching_parser(parsers, source)
        if parser is None and not pending_reply:
            return
        tool = FunctionTool(
            name="read_shared_link",
            description="在需要回答分享内容时读取当前消息、引用消息或本会话最近打开的分享。直接回答问题，无需转发文章或图片。",
            parameters={
                "type": "object",
                "properties": {
                    "image_number": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "0 或省略表示只读正文；看指定图片时填从 1 开始的原文图序。",
                    }
                },
            },
            handler=partial(
                self.read,
                source=source,
                parser=parser,
                parsers=parsers,
                pending_reply=pending_reply,
                key=key,
            ),
        )
        # 每个工具绑定本次来源，不修改可能被其他请求共享的工具集合。
        req.func_tool = ToolSet(
            tools=list(req.func_tool.tools) if req.func_tool else []
        )
        req.func_tool.add_tool(tool)

    async def read(
        self, event, image_number=0, *, source, parser, parsers, pending_reply, key
    ):
        """后台读取正文或一张图，把媒体固化后释放解析临时文件。"""
        if type(image_number) is not int or image_number < 0:
            return "图片序号须为非负整数，0 表示只读正文。"
        result = None
        token = requested_image_number.set(image_number)
        try:
            if pending_reply:
                params = {"self_id": event.get_self_id()} if event.get_self_id() else {}
                raw = await OneBotGateway.call(
                    event, "get_msg", message_id=int(pending_reply), **params
                )
                if not isinstance(raw, dict) or not _same_chat(raw, event):
                    return "无法取得本会话的引用消息。"
                source = context_from_segments(raw.get("message") or [])
                parser = await _matching_parser(parsers, source)
            if parser is None:
                return "引用消息中没有已启用平台支持的分享链接。"
            with TemporaryFileRegistry.capture() as files:
                result = await parser.parse(source)
                files.pop_all()
            if result.diagnostics.error:
                return result.diagnostics.error
            if key:
                self.recent[key] = source
                self.recent.move_to_end(key)
                if len(self.recent) > 128:
                    self.recent.popitem(last=False)
            return await _tool_content(result, image_number)
        except CookieAccessError as exc:
            return str(exc)
        except Exception as exc:
            logger.warning(f"读取分享失败: {type(exc).__name__}")
            return "分享读取失败，请检查链接或稍后重试。"
        finally:
            requested_image_number.reset(token)
            if result is not None:
                cleanup_temporary_files(result)


async def _matching_parser(parsers, source):
    if source.combined_text:
        for parser in parsers:
            if await parser.match(source):
                return parser
    return None


def _same_chat(raw, event):
    if group := event.get_group_id():
        return str(raw.get("group_id")) == str(group)
    return raw.get("message_type") == "private" and str(raw.get("user_id")) == str(
        event.get_sender_id()
    )


async def _tool_content(result: ParseResult, number: int) -> str | CallToolResult:
    slots = result.image_slots
    if not number:
        lines = [
            result.content.title,
            result.content.author,
            *result.visible_text_lines,
            f"配图共 {len(slots)} 张。",
        ]
        return "\n".join(line for line in lines if line)
    if number > len(slots):
        return f"原分享只有 {len(slots)} 张配图。"
    ref = slots[number - 1]
    # 图片只取自平台解析器已校验、下载的临时文件或内嵌数据。
    if ref.startswith(("base64://", "data:image/")) or ref in {
        str(path) for path in TemporaryFileRegistry.paths(result)
    }:
        image = await MediaResolver(ref, media_type="image").to_base64_data(
            strict=False
        )
        if image:
            return CallToolResult(
                content=[
                    TextContent(type="text", text=f"原分享第 {number} 张图"),
                    ImageContent(
                        type="image", mimeType=image.mime_type, data=image.base64_data
                    ),
                ]
            )
    return f"原分享第 {number} 张图无法读取。"
