"""识别知乎链接并分派到内容解析流程。"""

import re
from urllib.parse import urlparse

from ...core.contracts import ParseContext, ParseResult
from ...core.parser import BaseParser
from .request import ZhihuRequest
from .resolver import ZhihuContentResolver


class ZhihuParser(ZhihuContentResolver, BaseParser):
    """解析知乎问题、回答、专栏文章和想法。"""

    name = "zhihu"
    display_name = "知乎"
    cookie_config_key = "zhihu_cookies"
    image_host_suffixes = ("zhimg.com",)
    ANSWER_PATTERN = (
        r"https?://(?:www\.)?zhihu\.com/question/(?P<question_id>\d+)/"
        r"answer/(?P<answer_id>\d+)"
    )
    QUESTION_PATTERN = (
        r"https?://(?:www\.)?zhihu\.com/question/(?P<question_only_id>\d+)"
    )
    ARTICLE_PATTERN = r"https?://zhuanlan\.zhihu\.com/p/(?P<article_id>\d+)"
    TARDIS_ARTICLE_PATTERN = (
        r"https?://(?:www\.)?zhihu\.com/tardis/zm/art/"
        r"(?P<tardis_article_id>\d+)"
    )
    PIN_PATTERN = r"https?://(?:www\.)?zhihu\.com/pin/(?P<pin_id>\d+)"
    SHARE_PATTERN = r"https?://link\.zhihu\.com/\?[^\s#]+"
    PATTERNS = (
        ANSWER_PATTERN,
        QUESTION_PATTERN,
        ARTICLE_PATTERN,
        TARDIS_ARTICLE_PATTERN,
        PIN_PATTERN,
        SHARE_PATTERN,
    )

    async def match(self, context: ParseContext) -> bool:
        return any(
            re.search(pattern, context.combined_text) for pattern in self.PATTERNS
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        url = self._extract_url(context.combined_text)
        if not url:
            return ParseResult(platform=self.name, error="未找到知乎链接。")
        requester = ZhihuRequest(self.config)
        async with requester.create_client() as client:
            if re.search(self.SHARE_PATTERN, url):
                url = await requester.expand_share(client, url)
                if not self._is_trusted_zhihu_url(url):
                    raise ValueError("知乎分享链接跳转到不可信域名")
            result = await self._parse_url(requester, client, url)
            return await self.materialize_images(result, client, url)

    @classmethod
    def _extract_url(cls, text: str) -> str:
        for pattern in cls.PATTERNS:
            if match := re.search(pattern, text):
                return match.group(0)
        return ""

    @staticmethod
    def _is_trusted_zhihu_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "zhihu.com" or host.endswith(".zhihu.com")
