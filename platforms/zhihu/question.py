from ...core.contracts import OrderedContent, ParseResult
from ._result_builder import append_extra_videos, author_name, stats_line
from .common import normalize_text
from .content import parse_html_content


def parse_question_payload(payload: object, first_answer: object = None) -> ParseResult:
    """将知乎问题及默认首条回答转换为统一解析结果。"""
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
    author = author_name(payload.get("author"))

    if isinstance(first_answer, dict) and first_answer:
        answer_author = author_name(first_answer.get("author"))
        answer_contents, answer_videos = parse_html_content(
            str(first_answer.get("content") or "")
        )
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

    summary = stats_line(
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
        video_url=append_extra_videos(contents, videos),
        extra_lines=[summary] if summary else [],
    )
