from ...core.contracts import OrderedContent, ParseResult
from ._result_builder import append_extra_videos, author_name, stats_line
from .common import media_key, normalize_media_url, normalize_text
from .content import parse_html_content


def parse_pin_payload(payload: object) -> ParseResult:
    """将知乎想法载荷转换为统一解析结果。"""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("知乎想法数据为空")
    contents: list[OrderedContent] = []
    videos: list[str] = []
    seen_videos: set[str] = set()
    raw_content = payload.get("content")

    if isinstance(raw_content, str):
        raw_contents, raw_videos = parse_html_content(raw_content)
        contents.extend(raw_contents)
        videos.extend(raw_videos)
    elif isinstance(raw_content, list):
        for block in raw_content:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "").lower()
            if block_type in {"text", "paragraph"}:
                value = str(block.get("content") or block.get("text") or "")
                if "<" in value and ">" in value:
                    block_contents, block_videos = parse_html_content(value)
                    contents.extend(block_contents)
                    videos.extend(block_videos)
                elif text := normalize_text(value, keep_newlines=True):
                    contents.append(OrderedContent(kind="text", value=text))
            elif block_type in {"image", "img"}:
                image_url = normalize_media_url(
                    str(block.get("original_url") or block.get("url") or "")
                )
                if image_url:
                    contents.append(OrderedContent(kind="image", value=image_url))
            elif block_type == "video":
                for video_url in _find_video_urls(block):
                    key = media_key(video_url)
                    if key and key not in seen_videos:
                        seen_videos.add(key)
                        videos.append(video_url)

    summary = stats_line(
        payload,
        (
            ("赞同", ("voteupCount", "voteup_count")),
            ("评论", ("commentCount", "comment_count")),
        ),
    )
    return ParseResult(
        platform="zhihu",
        title=normalize_text(str(payload.get("title") or "")) or "知乎想法",
        author=author_name(payload.get("author")),
        ordered_contents=contents,
        video_url=append_extra_videos(contents, videos),
        extra_lines=[summary] if summary else [],
    )


def _find_video_urls(value: object) -> list[str]:
    found = []
    seen = set()

    def visit(current: object):
        if isinstance(current, str):
            candidate = normalize_media_url(current)
            lowered = candidate.lower()
            if candidate and (
                "video.zhihu.com" in lowered
                or any(
                    marker in lowered for marker in (".mp4", ".m3u8", ".mov", ".webm")
                )
            ):
                key = media_key(candidate)
                if key and key not in seen:
                    seen.add(key)
                    found.append(candidate)
            return
        if isinstance(current, dict):
            for nested in current.values():
                visit(nested)
        elif isinstance(current, list):
            for nested in current:
                visit(nested)

    visit(value)
    return found
