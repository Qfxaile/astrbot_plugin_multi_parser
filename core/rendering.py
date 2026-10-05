from astrbot.api.message_components import Image, Plain, Record, Video

from .contracts import ParseResult


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
    if include_summary:
        if result.title:
            lines.append(result.title)
        if result.author:
            lines.append(f"作者: {result.author}")
        if result.description:
            lines.append(f"简介:\n{result.description}")
        lines.extend(result.extra_lines)
        if result.error:
            lines.append(result.error)
        if result.video_url and include_video_url:
            lines.append(f"视频链接: {result.video_url}")
        if lines:
            summary_chain.append(Plain("\n".join(lines)))

    if result.ordered_contents:
        content_chain: list = []
        if include_content:
            for item in result.ordered_contents:
                if not item.value:
                    continue
                if item.kind == "image":
                    content_chain.append(_image_component(result, item.value))
                else:
                    content_chain.append(Plain(item.value))
        return [*summary_chain, *content_chain]

    content_chain: list = []
    if include_content:
        image_urls = [*result.cover_urls, *result.image_urls]
        for index, image_url in enumerate(image_urls):
            if image_url:
                content_chain.append(_image_component(result, image_url))
            elif error := result.image_errors.get(index):
                content_chain.append(Plain(error))
    return [*content_chain, *summary_chain]


def render_video_chain(result: ParseResult) -> list:
    return [Video.fromURL(result.video_url)] if result.video_url else []


def render_audio_chain(result: ParseResult) -> list:
    """将远程音频地址转换为 AstrBot 语音组件。"""
    return [Record.fromURL(result.audio_url)] if result.audio_url else []


def _image_component(result: ParseResult, value: str) -> Image:
    if any(value == str(path) for path in result.media_metadata.temporary_files):
        return Image.fromFileSystem(value)
    return Image(file=value)
