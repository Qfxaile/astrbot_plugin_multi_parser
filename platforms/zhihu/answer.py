from ...core.contracts import ParseResult
from ._result_builder import content_result


def parse_answer_payload(payload: object) -> ParseResult:
    """将知乎回答载荷转换为统一解析结果。"""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("知乎回答数据为空")
    question = payload.get("question")
    title = str(question.get("title") or "") if isinstance(question, dict) else ""
    return content_result(
        payload,
        title=title or "知乎回答",
        empty_message="知乎回答数据为空",
        stats=(
            ("赞同", ("voteupCount", "voteup_count")),
            ("评论", ("commentCount", "comment_count")),
            ("收藏", ("favoriteCount", "favorite_count", "favoritesCount")),
        ),
    )
