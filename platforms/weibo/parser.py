"""识别微博链接并分派到对应内容解析器。"""

import re

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.parser import BaseParser
from .article import WeiboArticleContent
from .client import resolve_share_url
from .common import WeiboContentSupport
from .post import WeiboPostContent
from .video import WeiboVideoContent


class WeiboParser(
    WeiboPostContent,
    WeiboArticleContent,
    WeiboVideoContent,
    WeiboContentSupport,
    BaseParser,
):
    """将微博链接路由到帖子、文章或独立视频解析器。"""

    name = "weibo"
    display_name = "微博"
    cookie_config_key = "weibo_cookies"
    image_host_suffixes = ("sinaimg.cn", "sinaimg.com")
    STATUS_PATTERNS = (
        r"https?://(?:www\.)?weibo\.com/\d+/(?P<desktop_id>[0-9A-Za-z]+)",
        r"https?://m\.weibo\.cn/(?:status|detail|\d+)/(?P<mobile_id>[0-9A-Za-z]+)",
    )
    TV_PATTERN = (
        r"https?://(?:www\.)?weibo\.com/tv/show/\d{4}:\d+"
        r"\?[^\s#]*\bmid=(?P<mid>\d+)[^\s#]*"
    )
    VIDEO_PATTERN = (
        r"https?://video\.weibo\.com/show\?[^\s#]*"
        r"\bfid=(?P<fid>\d+:\d+)[^\s#]*"
    )
    SHARE_PATTERN = r"https?://mapp\.api\.weibo\.cn/fx/[A-Za-z0-9]+\.html"
    ARTICLE_PATTERNS = (
        r"https?://(?:www\.)?weibo\.com/ttarticle/[^\s#]*[?&#]id=(?P<article_query_id>\d+)",
        r"https?://card\.weibo\.com/article/[^\s#]*/id/(?P<article_path_id>\d+)",
    )
    PATTERNS = (
        *STATUS_PATTERNS,
        TV_PATTERN,
        VIDEO_PATTERN,
        SHARE_PATTERN,
        *ARTICLE_PATTERNS,
    )
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    }

    async def match(self, context: ParseContext) -> bool:
        return any(
            re.search(pattern, context.combined_text) for pattern in self.PATTERNS
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        text = context.combined_text
        if match := re.search(self.TV_PATTERN, text):
            return await self._parse_status_id(self._mid_to_bid(match.group("mid")))
        if match := re.search(self.VIDEO_PATTERN, text):
            return await self._parse_video_fid(match.group("fid"))
        for pattern in self.ARTICLE_PATTERNS:
            if match := re.search(pattern, text):
                article_id = match.groupdict().get(
                    "article_query_id"
                ) or match.groupdict().get("article_path_id")
                return await self._parse_article(str(article_id))
        if match := re.search(self.SHARE_PATTERN, text):
            return await self._parse_share(match.group(0))
        for pattern in self.STATUS_PATTERNS:
            if match := re.search(pattern, text):
                status_id = match.groupdict().get(
                    "desktop_id"
                ) or match.groupdict().get("mobile_id")
                return await self._parse_status_id(str(status_id))
        return ParseResult(platform=self.name, error="未找到微博链接。")

    async def _parse_share(self, url: str) -> ParseResult:
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=True,
            headers=self.HEADERS,
            cookies=self._cookies(),
            **self.http_client_options,
        ) as client:
            final_url = await resolve_share_url(
                client,
                url,
                patterns=self.PATTERNS,
                trusted_url=self._is_trusted_weibo_url,
                auth_url=self._is_auth_url,
                raise_for_response=self.raise_for_response_status,
            )
        return await self.parse(ParseContext(text=final_url))
