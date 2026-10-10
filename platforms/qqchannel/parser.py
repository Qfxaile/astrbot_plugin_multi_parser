"""解析 QQ 中的腾讯频道帖子分享卡片。"""

import json
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser
from .client import request_feed
from .content import IMAGE_HOST_SUFFIXES, VIDEO_HOST_SUFFIXES, build_result


class QQChannelDetailError(ValueError):
    """表示公开帖子详情未返回可解析数据。"""


class QQChannelParser(BaseParser):
    """从 QQ JSON 分享卡片读取腾讯频道帖子的完整公开详情。"""

    name = "qqchannel"
    display_name = "腾讯频道"
    image_host_suffixes = IMAGE_HOST_SUFFIXES
    video_host_suffixes = VIDEO_HOST_SUFFIXES
    SHARE_HOST = "pd.qq.com"
    SHARE_PATH = "/qqweb/qunpro/share"
    DETAIL_URL = (
        "https://pd.qq.com/qunng/guild/gotrpc/noauth/"
        "trpc.qchannel.commreader.ComReader/GetFeedDetail?bkn&_v=1.0.1"
    )
    CONTENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
    ATTACHMENT_ID_PATTERN = re.compile(r"[A-Fa-f0-9]{1,128}\Z")
    FEED_ID_PATTERN = re.compile(r"B_[A-Za-z0-9_-]{1,190}\Z")
    MAX_URL_LENGTH = 8192
    MAX_TITLE_LENGTH = 300
    MAX_RESPONSE_BYTES = 4 * 1024 * 1024
    GUEST_UIN_MIN = 144115351284613120
    GUEST_UIN_MAX = 144115364169515007
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
            "Chrome/131.0 Mobile Safari/537.36"
        ),
    }

    async def match(self, context: ParseContext) -> bool:
        return self._find_card(context) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        card = self._find_card(context)
        if card is None:
            result = ParseResult(platform=self.name)
            result.diagnostics.error = "未找到可解析的腾讯频道分享卡片。"
            return result

        index, share_url = card
        title = self._clean_title(self._value_at(context.json_titles, index))
        cover_url = self._value_at(context.json_previews, index)
        if len(cover_url) > self.MAX_URL_LENGTH or not is_trusted_https_url(
            cover_url,
            self.image_host_suffixes,
            allow_fragment=False,
        ):
            cover_url = ""

        fallback = ParseResult(platform=self.name)
        fallback.content.title = title or "腾讯频道帖子"
        if cover_url:
            fallback.content.cover_urls.append(cover_url)
        feed_id = self._feed_id_at(context, index)
        if not self.FEED_ID_PATTERN.fullmatch(feed_id):
            if not cover_url:
                return fallback
            return await self.materialize_public_images(
                fallback,
                share_url,
                headers=self.HEADERS,
            )

        async with self.http_client(headers=self.HEADERS) as client:
            try:
                feed = await request_feed(
                    client,
                    self.DETAIL_URL,
                    share_url,
                    feed_id,
                    guest_uin_min=self.GUEST_UIN_MIN,
                    guest_uin_max=self.GUEST_UIN_MAX,
                    max_response_bytes=self.MAX_RESPONSE_BYTES,
                    raise_for_response=self.raise_for_response_status,
                    mapping=self._mapping,
                )
                result = build_result(feed, fallback_title=title)
                result.media.video_download_headers = {
                    "Referer": share_url,
                    "User-Agent": self.HEADERS["User-Agent"],
                }
            except (
                httpx.HTTPError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                QQChannelDetailError,
                ValueError,
            ):
                fallback.content.extra_lines.append(
                    "帖子详情获取失败，已返回分享卡片摘要。"
                )
                result = fallback
        return await self.materialize_public_images(
            result,
            share_url,
            headers=self.HEADERS,
        )

    @classmethod
    def _find_card(cls, context: ParseContext) -> tuple[int, str] | None:
        for index, url in enumerate(context.json_urls):
            if cls._is_valid_share_url(url):
                return index, url
        return None

    @classmethod
    def _is_valid_share_url(cls, url: str) -> bool:
        if not url or len(url) > cls.MAX_URL_LENGTH:
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        if (
            parsed.hostname != cls.SHARE_HOST
            or parsed.path != cls.SHARE_PATH
            or not is_trusted_https_url(
                url,
                (cls.SHARE_HOST,),
                allow_fragment=False,
            )
        ):
            return False

        query = parse_qs(parsed.query, keep_blank_values=True)
        content_ids = query.get("contentID", [])
        attachment_ids = query.get("attaContentID", [])
        return (
            len(content_ids) == 1
            and cls.CONTENT_ID_PATTERN.fullmatch(content_ids[0]) is not None
        ) or (
            len(attachment_ids) == 1
            and cls.ATTACHMENT_ID_PATTERN.fullmatch(attachment_ids[0]) is not None
        )

    @classmethod
    def _clean_title(cls, value: str) -> str:
        return " ".join(str(value or "").split())[: cls.MAX_TITLE_LENGTH]

    @staticmethod
    def _value_at(values: list[str], index: int) -> str:
        return str(values[index]) if index < len(values) else ""

    @staticmethod
    def _feed_id_at(context: ParseContext, index: int) -> str:
        if index >= len(context.json_metadata):
            return ""
        return str(context.json_metadata[index].get("feed_id") or "")

    @staticmethod
    def _mapping(value: object) -> Mapping[str, object]:
        return value if isinstance(value, Mapping) else {}
