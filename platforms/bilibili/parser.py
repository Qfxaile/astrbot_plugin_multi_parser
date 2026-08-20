"""识别 B站链接并分派到对应内容解析器。"""

import re

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
    ID_PATTERN = r"(BV[0-9A-Za-z]{10}|av\d+)"
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
        text = context.combined_text
        return find_mall_target(text) is not None or any(
            re.search(pattern, text)
            for pattern in (
                self.DYNAMIC_PATTERN,
                self.OPUS_PATTERN,
                self.ARTICLE_PATTERN,
                self.LIVE_PATTERN,
                self.BANGUMI_PATTERN,
                self.ID_PATTERN,
                self.SHORT_PATTERN,
            )
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        text = context.combined_text
        if mall_target := find_mall_target(text):
            return await self._parse_mall(mall_target)
        if match := re.search(self.DYNAMIC_PATTERN, text):
            return await self._parse_dynamic(match.group("dynamic_id"))
        if match := re.search(self.OPUS_PATTERN, text):
            return await self._parse_opus(match.group("opus_id"))
        if match := re.search(self.ARTICLE_PATTERN, text):
            return await self._parse_article(match.group("article_id"))
        if match := re.search(self.LIVE_PATTERN, text):
            return await self._parse_live(match.group("room_id"))
        if match := re.search(self.BANGUMI_PATTERN, text):
            return await self._parse_bangumi(
                match.group("bangumi_kind"),
                match.group("bangumi_id"),
            )

        match = re.search(self.ID_PATTERN, text)
        if not match and (short_match := re.search(self.SHORT_PATTERN, text)):
            headers = self._headers("https://www.bilibili.com")
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=True,
                **self.http_client_options,
            ) as client:
                response = await client.get(short_match.group(0), headers=headers)
            final_url = str(response.url)
            if final_url == short_match.group(0):
                self.raise_for_response_status(response)
                return ParseResult(platform=self.name, error="B站短链未发生跳转。")
            return await self.parse(ParseContext(text=final_url))

        video_id = match.group(0) if match else ""
        if not video_id:
            return ParseResult(platform=self.name, error="未找到 B站 视频 ID。")
        return await self._parse_video(video_id)
