from collections.abc import Iterable

from ...core.contracts import OrderedContent, ParseResult
from .common import format_count, media_key, normalize_media_url, normalize_text
from .content import parse_html_content


def _author_name(value: object) -> str:
    if not isinstance(value, dict):
        return "未知作者"
    return (
        normalize_text(str(value.get("name") or value.get("username") or ""))
        or "未知作者"
    )


def _first_value(payload: dict, *keys: str):
    for key in keys:
        if key in payload and payload[key] is not None:
            return payload[key]
    return None


def _stats_line(payload: dict, fields: Iterable[tuple[str, tuple[str, ...]]]) -> str:
    parts = []
    for label, keys in fields:
        value = _first_value(payload, *keys)
        if value is not None:
            parts.append(f"{label} {format_count(value)}")
    return " | ".join(parts)


def _append_extra_videos(contents: list[OrderedContent], video_urls: list[str]) -> str:
    if not video_urls:
        return ""
    contents.extend(
        OrderedContent(kind="text", value=f"视频链接: {url}") for url in video_urls[1:]
    )
    return video_urls[0]


def _content_result(
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
    stats_line = _stats_line(payload, stats)
    return ParseResult(
        platform="zhihu",
        title=normalize_text(title),
        author=_author_name(payload.get("author")),
        ordered_contents=contents,
        video_url=_append_extra_videos(contents, video_urls),
        extra_lines=[stats_line] if stats_line else [],
    )


def parse_answer_payload(payload: object) -> ParseResult:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("知乎回答数据为空")
    question = payload.get("question")
    title = str(question.get("title") or "") if isinstance(question, dict) else ""
    return _content_result(
        payload,
        title=title or "知乎回答",
        empty_message="知乎回答数据为空",
        stats=(
            ("赞同", ("voteupCount", "voteup_count")),
            ("评论", ("commentCount", "comment_count")),
            ("收藏", ("favoriteCount", "favorite_count", "favoritesCount")),
        ),
    )


def parse_question_payload(payload: object, first_answer: object = None) -> ParseResult:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("知乎问题数据为空")
    title = normalize_text(str(payload.get("title") or "")) or "知乎问题"
    detail = str(
        payload.get("detail")
        or payload.get("description")
        or payload.get("content")
        or ""
    )
    contents, videos = parse_html_content(detail)
    author = _author_name(payload.get("author"))

    if isinstance(first_answer, dict) and first_answer:
        answer_author = _author_name(first_answer.get("author"))
        answer_body = str(first_answer.get("content") or "")
        answer_contents, answer_videos = parse_html_content(answer_body)
        if answer_contents or answer_videos:
            contents.append(
                OrderedContent(
                    kind="text",
                    value=f"默认排序首条回答 @{answer_author}",
                )
            )
            contents.extend(answer_contents)
            videos.extend(answer_videos)
            author = answer_author

    stats_line = _stats_line(
        payload,
        (
            ("回答", ("answerCount", "answer_count")),
            ("关注", ("followerCount", "follower_count")),
            ("浏览", ("visitCount", "visit_count")),
        ),
    )
    return ParseResult(
        platform="zhihu",
        title=title,
        author=author,
        ordered_contents=contents,
        video_url=_append_extra_videos(contents, videos),
        extra_lines=[stats_line] if stats_line else [],
    )


def parse_article_payload(payload: object) -> ParseResult:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("知乎文章数据为空")
    return _content_result(
        payload,
        title=str(payload.get("title") or "知乎文章"),
        empty_message="知乎文章数据为空",
        stats=(
            ("赞同", ("voteupCount", "voteup_count")),
            ("评论", ("commentCount", "comment_count")),
            ("收藏", ("favoriteCount", "favorite_count", "favoritesCount")),
        ),
    )


def parse_pin_payload(payload: object) -> ParseResult:
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

    stats_line = _stats_line(
        payload,
        (
            ("赞同", ("voteupCount", "voteup_count")),
            ("评论", ("commentCount", "comment_count")),
        ),
    )
    return ParseResult(
        platform="zhihu",
        title=normalize_text(str(payload.get("title") or "")) or "知乎想法",
        author=_author_name(payload.get("author")),
        ordered_contents=contents,
        video_url=_append_extra_videos(contents, videos),
        extra_lines=[stats_line] if stats_line else [],
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
