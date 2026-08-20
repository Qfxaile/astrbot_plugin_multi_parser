"""解析 QQ 中的腾讯频道帖子分享卡片。"""

import re
from urllib.parse import parse_qs, urlsplit

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser


class QQChannelParser(BaseParser):
    """从 QQ JSON 分享卡片展示腾讯频道帖子的公开摘要。"""

    name = "qqchannel"
    display_name = "腾讯频道"
    image_host_suffixes = ("qpic.cn",)
    SHARE_HOST = "pd.qq.com"
    SHARE_PATH = "/qqweb/qunpro/share"
    CONTENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
    ATTACHMENT_ID_PATTERN = re.compile(r"[A-Fa-f0-9]{1,128}\Z")
    MAX_URL_LENGTH = 8192
    MAX_TITLE_LENGTH = 300
    HEADERS = {
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
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

        result = ParseResult(
            platform=self.name,
            title=title or "腾讯频道帖子",
            cover_urls=[cover_url] if cover_url else [],
        )
        if not cover_url:
            return result
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
