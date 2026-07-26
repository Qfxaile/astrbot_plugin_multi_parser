from ...core.contracts import ParseResult
from ._result_builder import content_result


def parse_article_payload(payload: object) -> ParseResult:
    """将知乎文章载荷转换为统一解析结果。"""
    if not isinstance(payload, dict) or not payload:
        raise ValueError("知乎文章数据为空")
    return content_result(
        payload,
        title=str(payload.get("title") or "知乎文章"),
        empty_message="知乎文章数据为空",
        stats=(
            ("赞同", ("voteupCount", "voteup_count")),
            ("评论", ("commentCount", "comment_count")),
            ("收藏", ("favoriteCount", "favorite_count", "favoritesCount")),
        ),
    )
