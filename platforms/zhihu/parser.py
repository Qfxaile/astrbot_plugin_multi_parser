"""识别知乎链接并分派到内容解析流程。"""

import re
from collections.abc import Iterator
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
    ROUTES = (
        ("question_answer", ANSWER_PATTERN),
        ("question_answer", QUESTION_PATTERN),
        ("article", ARTICLE_PATTERN),
        ("article", TARDIS_ARTICLE_PATTERN),
        ("idea", PIN_PATTERN),
    )

    async def match(self, context: ParseContext) -> bool:
        return any(self._route_enabled(kind) for kind, _ in self._routes(context))

    def _route_enabled(self, kind: str | None) -> bool:
        if kind is not None:
            return self.parse_feature_enabled(kind)
        return any(
            self.parse_feature_enabled(feature)
            for feature in ("question_answer", "article", "idea")
        )

    def _routes(self, context: ParseContext) -> Iterator[tuple[str | None, str]]:
        text = context.combined_text
        for kind, pattern in self.ROUTES:
            if match := re.search(pattern, text):
                yield kind, match.group(0)
        for match in re.finditer(self.SHARE_PATTERN, text):
            yield None, match.group(0)

    async def parse(self, context: ParseContext) -> ParseResult:
        matched = False
        for kind, url in self._routes(context):
            matched = True
            if self._route_enabled(kind):
                result = await self._parse_candidate(url, is_share=kind is None)
                if not result.diagnostics.skipped:
                    return result
        if matched:
            return ParseResult.skipped(self.name)
        result = ParseResult(platform=self.name)
        result.diagnostics.error = "未找到知乎链接。"
        return result

    async def _parse_candidate(self, url: str, *, is_share: bool) -> ParseResult:
        requester = ZhihuRequest(self.config)
        async with requester.create_client() as client:
            if is_share:
                url = await requester.expand_share(client, url)
                if not self._is_trusted_zhihu_url(url):
                    raise ValueError("知乎分享链接跳转到不可信域名")
                for kind, pattern in self.ROUTES:
                    if re.search(pattern, url):
                        if not self.parse_feature_enabled(kind):
                            return ParseResult.skipped(self.name)
                        break
            result = await self._parse_url(requester, client, url)
            return await self.materialize_images(result, client, url)

    @staticmethod
    def _is_trusted_zhihu_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "zhihu.com" or host.endswith(".zhihu.com")
