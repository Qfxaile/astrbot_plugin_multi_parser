from collections.abc import Iterable

from ...core.contracts import OrderedContent, ParseResult
from .common import format_count, normalize_text
from .content import parse_html_content


def author_name(value: object) -> str:
    if not isinstance(value, dict):
        return "未知作者"
    return (
        normalize_text(str(value.get("name") or value.get("username") or ""))
        or "未知作者"
    )


def first_value(payload: dict, *keys: str):
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def stats_line(payload: dict, fields: Iterable[tuple[str, tuple[str, ...]]]) -> str:
    parts = []
    for label, keys in fields:
        value = first_value(payload, *keys)
        if value is not None:
            parts.append(f"{label} {format_count(value)}")
    return " | ".join(parts)


def append_extra_videos(contents: list[OrderedContent], video_urls: list[str]) -> str:
    if not video_urls:
        return ""
    contents.extend(
        OrderedContent(kind="text", value=f"视频链接: {url}") for url in video_urls[1:]
    )
    return video_urls[0]


def content_result(
    payload: dict,
    *,
    title: str,
    empty_message: str,
    stats: Iterable[tuple[str, tuple[str, ...]]],
) -> ParseResult:
    if not payload:
        raise ValueError(empty_message)
    html_body = str(
        payload.get("content")
        or payload.get("contentHtml")
        or payload.get("content_html")
        or ""
    )
    contents, video_urls = parse_html_content(html_body)
    summary = stats_line(payload, stats)
    return ParseResult(
        platform="zhihu",
        title=normalize_text(title),
        author=author_name(payload.get("author")),
        ordered_contents=contents,
        video_url=append_extra_videos(contents, video_urls),
        extra_lines=[summary] if summary else [],
    )
