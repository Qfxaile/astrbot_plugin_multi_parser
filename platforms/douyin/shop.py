"""解析抖音商城分享链接中的公开商品卡片。"""

import json
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

from ...core.contracts import ParseResult
from ...core.http import is_trusted_https_url
from ...core.product_metadata import clean_product_text


class DouyinShopContent:
    """从抖音商城详情 URL 提取商品标题和主图。"""

    SHOP_HOST = "haohuo.jinritemai.com"
    SHOP_PATH = "/ecommerce/trade/detail/index.html"
    SHOP_IMAGE_HOST_SUFFIXES = ("ecombdimg.com",)

    @classmethod
    def _is_shop_url(cls, url: str) -> bool:
        if not is_trusted_https_url(url, (cls.SHOP_HOST,)):
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return parsed.hostname == cls.SHOP_HOST and parsed.path == cls.SHOP_PATH

    def _parse_shop_url(self, url: str) -> ParseResult:
        """解析商城分享 URL 内嵌的公开商品信息。"""
        if not self._is_shop_url(url):
            return self._shop_metadata_error()

        values = parse_qs(urlsplit(url).query, keep_blank_values=True).get(
            "goods_detail", []
        )
        if len(values) != 1:
            return self._shop_metadata_error()
        try:
            payload = json.loads(values[0])
        except (json.JSONDecodeError, RecursionError):
            return self._shop_metadata_error()
        if not isinstance(payload, Mapping):
            return self._shop_metadata_error()

        title = clean_product_text(payload.get("title"))
        if not title:
            return self._shop_metadata_error()

        image_url = self._shop_image_url(payload.get("img"))
        return ParseResult(
            platform=self.name,
            title=title,
            cover_urls=[image_url] if image_url else [],
            extra_lines=[],
        )

    @classmethod
    def _shop_image_url(cls, value: object) -> str:
        if not isinstance(value, Mapping):
            return ""
        candidates = value.get("url_list")
        if not isinstance(candidates, list):
            return ""
        for candidate in candidates:
            if isinstance(candidate, str) and is_trusted_https_url(
                candidate, cls.SHOP_IMAGE_HOST_SUFFIXES
            ):
                return candidate
        return ""

    def _shop_metadata_error(self) -> ParseResult:
        return ParseResult(
            platform=self.name,
            error="未找到抖音商城商品信息，链接可能已失效。",
        )
