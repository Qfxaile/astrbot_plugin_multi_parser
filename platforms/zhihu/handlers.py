"""兼容旧的知乎内容处理器导入路径。"""

from .answer import parse_answer_payload
from .article import parse_article_payload
from .pin import parse_pin_payload
from .question import parse_question_payload

__all__ = [
    "parse_answer_payload",
    "parse_article_payload",
    "parse_pin_payload",
    "parse_question_payload",
]
