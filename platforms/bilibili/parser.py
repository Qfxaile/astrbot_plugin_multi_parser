"""识别 B站链接并分派到对应内容解析器。"""

import re
from collections.abc import Awaitable, Callable, Iterator
from functools import partial

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.parser import BaseParser
from .article import BilibiliArticleContent
from .bangumi import BilibiliBangumiContent
from .common import BilibiliContentSupport, original_image_url
from .dynamic import BilibiliDynamicContent
from .live import BilibiliLiveContent
from .mall import BilibiliMallContent, find_mall_target
from .video import BilibiliVideoContent

_original_image_url = original_image_url


class BilibiliParser(
    BilibiliMallContent,
    BilibiliLiveContent,
    BilibiliBangumiContent,
    BilibiliDynamicContent,
    BilibiliArticleContent,
    BilibiliVideoContent,
    BilibiliContentSupport,
    BaseParser,
):
    """将 B站链接路由到视频、影视、直播、图文、文章或会员购解析器。"""

    name = "bilibili"
    display_name = "B站"
    cookie_config_key = "bilibili_cookies"
    cookie_failure_status_codes = frozenset({401, 403, 412})
    image_host_suffixes = ("hdslb.com",)
    SHORT_PATTERN = r"https?://(?:bili2233\.cn|b23\.tv)/[a-zA-Z0-9]+"
    ID_PATTERN = r"(?<![A-Za-z0-9])(BV[0-9A-Za-z]{10}|av\d+)(?![A-Za-z0-9])"
    DYNAMIC_PATTERN = (
        r"https?://(?:t\.bilibili\.com/|www\.bilibili\.com/dynamic/)"
        r"(?P<dynamic_id>\d+)"
    )
    OPUS_PATTERN = r"https?://www\.bilibili\.com/opus/(?P<opus_id>\d+)"
    ARTICLE_PATTERN = r"https?://www\.bilibili\.com/read/cv(?P<article_id>\d+)"
    LIVE_PATTERN = r"https?://live\.bilibili\.com/(?P<room_id>\d+)"
    BANGUMI_PATTERN = (
        r"https?://www\.bilibili\.com/bangumi/play/"
        r"(?P<bangumi_kind>ep|ss)(?P<bangumi_id>\d+)"
    )

    async def match(self, context: ParseContext) -> bool:
        return any(
            self._route_enabled(kind) for kind, _ in self._routes(context.combined_text)
        )

    def _route_enabled(self, kind: str | None) -> bool:
        if kind is not None:
            return self.parse_feature_enabled(kind)
        return any(
            self.parse_feature_enabled(feature)
            for feature in ("video", "dynamic", "article", "live", "bangumi", "mall")
        )

    def _routes(
        self, text: str
    ) -> Iterator[tuple[str | None, Callable[[], Awaitable[ParseResult]]]]:
        """保留直接链接的优先级，最后尝试需要展开的短链。"""
        if mall_target := find_mall_target(text):
            yield "mall", partial(self._parse_mall, mall_target)
        for kind, pattern, handler in (
            ("dynamic", self.DYNAMIC_PATTERN, self._parse_dynamic),
            ("dynamic", self.OPUS_PATTERN, self._parse_opus),
            ("article", self.ARTICLE_PATTERN, self._parse_article),
            ("live", self.LIVE_PATTERN, self._parse_live),
            ("bangumi", self.BANGUMI_PATTERN, self._parse_bangumi),
            ("video", self.ID_PATTERN, self._parse_video),
        ):
            if match := re.search(pattern, text):
                yield kind, partial(handler, *match.groups())
        for match in re.finditer(self.SHORT_PATTERN, text):
            yield None, partial(self._parse_short, match.group(0))

    async def parse(self, context: ParseContext) -> ParseResult:
        matched = False
        for kind, handler in self._routes(context.combined_text):
            matched = True
            if self._route_enabled(kind):
                result = await handler()
                if not result.diagnostics.skipped:
                    return result
        if matched:
            return ParseResult.skipped(self.name)
        result = ParseResult(platform=self.name)
        result.diagnostics.error = "未找到 B站 视频 ID。"
        return result

    async def _parse_short(self, url: str) -> ParseResult:
        headers = self._headers("https://www.bilibili.com")
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=True,
            **self.http_client_options,
        ) as client:
            response = await client.get(url, headers=headers)
        final_url = str(response.url)
        if final_url == url:
            self.raise_for_response_status(response)
            result = ParseResult(platform=self.name)
            result.diagnostics.error = "B站短链未发生跳转。"
            return result
        return await self.parse(ParseContext(text=final_url))
