"""解析 QQ 中的腾讯频道帖子分享卡片。"""

import json
import re
import secrets
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser
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
            return ParseResult(
                platform=self.name,
                error="未找到可解析的腾讯频道分享卡片。",
            )

        index, share_url = card
        title = self._clean_title(self._value_at(context.json_titles, index))
        cover_url = self._value_at(context.json_previews, index)
        if len(cover_url) > self.MAX_URL_LENGTH or not is_trusted_https_url(
            cover_url,
            self.image_host_suffixes,
            allow_fragment=False,
        ):
            cover_url = ""

        fallback = ParseResult(
            platform=self.name,
            title=title or "腾讯频道帖子",
            cover_urls=[cover_url] if cover_url else [],
        )
        feed_id = self._feed_id_at(context, index)
        if not self.FEED_ID_PATTERN.fullmatch(feed_id):
            if not cover_url:
                return fallback
            return await self.materialize_public_images(
                fallback,
                share_url,
                headers=self.HEADERS,
            )

        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=False,
            headers=self.HEADERS,
            **self.http_client_options,
        ) as client:
            try:
                feed = await self._request_feed(client, share_url, feed_id)
                result = build_result(feed, fallback_title=title)
                result.video_download_headers = {
                    "Referer": share_url,
                    "User-Agent": self.HEADERS["User-Agent"],
                }
            except (
                httpx.HTTPError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                QQChannelDetailError,
            ):
                fallback.extra_lines.append("帖子详情获取失败，已返回分享卡片摘要。")
                result = fallback
        return await self.materialize_public_images(
            result,
            share_url,
            headers=self.HEADERS,
        )

    async def _request_feed(
        self,
        client: httpx.AsyncClient,
        share_url: str,
        feed_id: str,
    ) -> Mapping[str, object]:
        """通过腾讯频道公开访客接口读取限长帖子详情。"""
        guest_uin = str(
            self.GUEST_UIN_MIN
            + secrets.randbelow(self.GUEST_UIN_MAX - self.GUEST_UIN_MIN + 1)
        )
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Cookie": f"uuid={guest_uin}; p_uin={guest_uin}",
            "Referer": share_url,
            "X-Oidb": '{"uint32_command":"0x10f4","uint32_service_type":14}',
            "X-QQ-Client-AppId": "537246381",
        }
        body = {
            "feedId": feed_id,
            "from": 2,
            "detail_type": 1,
            "content_type": 2,
            "channelSign": {},
            "extInfo": {
                "mapInfo": [
                    {"key": "qc-tabid", "value": "ark"},
                    {"key": "qc-pageid", "value": "pc"},
                ]
            },
        }
        async with client.stream(
            "POST",
            self.DETAIL_URL,
            headers=headers,
            json=body,
        ) as response:
            self.raise_for_response_status(response)
            content_length = response.headers.get("Content-Length", "")
            if (
                content_length.isdigit()
                and int(content_length) > self.MAX_RESPONSE_BYTES
            ):
                raise QQChannelDetailError("帖子详情响应超过安全限制")
            chunks: list[bytes] = []
            received = 0
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                received += len(chunk)
                if received > self.MAX_RESPONSE_BYTES:
                    raise QQChannelDetailError("帖子详情响应超过安全限制")
                chunks.append(chunk)

        payload = json.loads(b"".join(chunks))
        if not isinstance(payload, Mapping) or payload.get("retcode") != 0:
            raise QQChannelDetailError("帖子详情接口返回失败")
        feed = self._mapping(self._mapping(payload.get("data")).get("feed"))
        if not feed:
            raise QQChannelDetailError("帖子详情为空")
        return feed

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
