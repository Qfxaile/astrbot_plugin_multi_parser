from astrbot.api.message_components import Image, Plain, Record, Video

from ..contracts import ParseResult
from ..media import TemporaryFileRegistry


class ParseResultRenderer:
    """将统一解析结果渲染为 AstrBot 消息组件。"""

    @staticmethod
    def info_chain(
        result: ParseResult,
        *,
        include_video_url: bool = False,
        include_summary: bool = True,
        include_content: bool = True,
    ) -> list:
        return render_info_chain(
            result,
            include_video_url=include_video_url,
            include_summary=include_summary,
            include_content=include_content,
        )

    @staticmethod
    def video_chain(result: ParseResult) -> list:
        return render_video_chain(result)

    @staticmethod
    def audio_chain(result: ParseResult) -> list:
        return render_audio_chain(result)


def render_info_chain(
    result: ParseResult,
    *,
    include_video_url: bool = False,
    include_summary: bool = True,
    include_content: bool = True,
) -> list:
    """将平台无关的解析结果转换为 AstrBot 消息组件。"""
    summary_chain: list = []
    lines = []
    content = result.content
    diagnostics = result.diagnostics
    media = result.media
    if include_summary:
        if content.title:
            lines.append(content.title)
        if content.author:
            lines.append(f"作者: {content.author}")
        if content.description:
            lines.append(f"简介:\n{content.description}")
        lines.extend(content.extra_lines)
        if diagnostics.error:
            lines.append(diagnostics.error)
        if media.video_url and include_video_url:
            lines.append(f"视频链接: {media.video_url}")
        if lines:
            summary_chain.append(Plain("\n".join(lines)))

    if content.ordered_contents:
        content_chain: list = []
        if include_content:
            for item in content.ordered_contents:
                if not item.value:
                    continue
                if item.kind == "image":
                    content_chain.append(_image_component(result, item.value))
                else:
                    content_chain.append(Plain(item.value))
        return [*summary_chain, *content_chain]

    content_chain: list = []
    if include_content:
        image_urls = [*content.cover_urls, *content.image_urls]
        for index, image_url in enumerate(image_urls):
            if image_url:
                content_chain.append(_image_component(result, image_url))
            elif error := diagnostics.image_errors.get(index):
                content_chain.append(Plain(error))
    return [*content_chain, *summary_chain]


def render_video_chain(result: ParseResult) -> list:
    return [Video.fromURL(result.media.video_url)] if result.media.video_url else []


def render_audio_chain(result: ParseResult) -> list:
    """将远程音频地址转换为 AstrBot 语音组件。"""
    return [Record.fromURL(result.media.audio_url)] if result.media.audio_url else []


def _image_component(result: ParseResult, value: str) -> Image:
    if any(value == str(path) for path in TemporaryFileRegistry.paths(result)):
        return Image.fromFileSystem(value)
    return Image(file=value)
