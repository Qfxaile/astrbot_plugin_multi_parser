"""解析淘宝与天猫匿名可访问的公开商品页。"""

import re
from collections.abc import Iterable, Mapping
from html import unescape
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value, is_trusted_https_url
from ...core.parser import BaseParser
from ...core.product_metadata import (
    ProductMetadata,
    clean_product_text,
    extract_json_ld_product,
    extract_open_graph_product,
    format_product_price,
    iter_json_script_values,
)
from ...core.webpage import TrustedWebPageError, fetch_trusted_html


class TaobaoParser(BaseParser):
    """解析匿名可访问的淘宝与天猫公开商品页。"""

    name = "taobao"
    display_name = "淘宝/天猫"
    cookie_config_key = "taobao_cookies"
    cookie_domains = (".taobao.com", ".tmall.com", ".tb.cn")
    page_host_suffixes = ("taobao.com", "tmall.com", "tb.cn")
    image_host_suffixes = ("alicdn.com", "tbcdn.cn")
    URL_PATTERN = re.compile(
        r"https://[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    ITEM_ID_PATTERN = re.compile(r"\d{1,32}\Z")
    SHORT_HOSTS = frozenset({"m.tb.cn", "e.tb.cn"})
    SHARE_TARGET_PATTERN = re.compile(
        r"\bvar\s+url\s*=\s*(?P<quote>['\"])(?P<url>https://[^'\"\r\n]{1,8192})(?P=quote)\s*;",
        re.IGNORECASE,
    )
    PRODUCT_PATHS = {
        "item.taobao.com": frozenset({"/item.htm"}),
        "detail.tmall.com": frozenset({"/item.htm"}),
        "h5.m.taobao.com": frozenset({"/awp/core/detail.htm"}),
        "main.m.taobao.com": frozenset({"/security-h5-detail/home"}),
    }
    VERIFY_MARKERS = ("验证码", "安全验证", "登录后查看")
    HEADERS = {
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
            "Chrome/131.0 Mobile Safari/537.36"
        ),
    }

    async def match(self, context: ParseContext) -> bool:
        return self._find_product_url(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        url = self._find_product_url(context.combined_text)
        if url is None:
            return ParseResult(platform=self.name, error="未找到淘宝/天猫商品链接。")

        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
                cookies=build_cookies(
                    cookie_config_value(self.config, self.cookie_config_key),
                    self.cookie_domains,
                ),
            ) as client:
                page = await fetch_trusted_html(
                    client,
                    url,
                    self.page_host_suffixes,
                )
                canonical_url = self._canonical_product_url(page.final_url)
                if not canonical_url:
                    product_url = self._client_side_product_url(
                        page.final_url,
                        page.html,
                    )
                    if product_url:
                        page = await fetch_trusted_html(
                            client,
                            product_url,
                            self.page_host_suffixes,
                        )
                        canonical_url = self._canonical_product_url(page.final_url)
                if not canonical_url:
                    return ParseResult(
                        platform=self.name,
                        error="淘宝/天猫分享链接未指向受支持的商品。",
                    )
                if any(marker in page.html for marker in self.VERIFY_MARKERS):
                    return ParseResult(
                        platform=self.name,
                        error=str(self.cookie_access_error()),
                    )

                metadata = extract_json_ld_product(
                    page.html,
                    page.final_url,
                )
                metadata = metadata.with_fallback(
                    self._extract_platform_metadata(page.html, page.final_url)
                )
                metadata = metadata.with_fallback(
                    extract_open_graph_product(page.html, page.final_url)
                )
                if not metadata.title:
                    return ParseResult(
                        platform=self.name,
                        error=str(self.cookie_access_error()),
                    )

                result = self._build_result(metadata, canonical_url)
                if not result.cover_urls:
                    return result
                return await self.materialize_public_images(
                    result,
                    page.final_url,
                    headers=self.HEADERS,
                )
        except TrustedWebPageError as exc:
            return ParseResult(platform=self.name, error=str(exc))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in self.cookie_failure_status_codes:
                return ParseResult(
                    platform=self.name,
                    error=str(self.cookie_access_error()),
                )
            return ParseResult(
                platform=self.name,
                error="淘宝/天猫商品请求失败，请稍后重试。",
            )
        except httpx.HTTPError:
            return ParseResult(
                platform=self.name,
                error="淘宝/天猫商品请求失败，请稍后重试。",
            )

    @classmethod
    def _find_product_url(cls, text: str) -> str | None:
        for match in cls.URL_PATTERN.finditer(text):
            url = match.group(0).rstrip(".,!?;:，。！？；：）】》")
            if cls._is_supported_url(url):
                return url
        return None

    @classmethod
    def _is_supported_url(cls, url: str) -> bool:
        if not is_trusted_https_url(url, cls.page_host_suffixes):
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        host = (parsed.hostname or "").lower()
        if host in cls.SHORT_HOSTS:
            return parsed.path not in {"", "/"}
        return cls._product_id(url) is not None

    @classmethod
    def _product_id(cls, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
        if parsed.path not in cls.PRODUCT_PATHS.get(host, frozenset()):
            return None
        item_ids = parse_qs(parsed.query, keep_blank_values=True).get("id", [])
        if len(item_ids) != 1 or cls.ITEM_ID_PATTERN.fullmatch(item_ids[0]) is None:
            return None
        return item_ids[0]

    @classmethod
    def _canonical_product_url(cls, url: str) -> str | None:
        item_id = cls._product_id(url)
        if item_id is None:
            return None
        host = (urlsplit(url).hostname or "").lower()
        if host == "detail.tmall.com":
            return f"https://detail.tmall.com/item.htm?id={item_id}"
        return f"https://item.taobao.com/item.htm?id={item_id}"

    @classmethod
    def _client_side_product_url(
        cls,
        page_url: str,
        html_text: str,
    ) -> str | None:
        try:
            host = (urlsplit(page_url).hostname or "").lower()
        except ValueError:
            return None
        if host not in cls.SHORT_HOSTS:
            return None
        match = cls.SHARE_TARGET_PATTERN.search(html_text)
        if match is None:
            return None
        target = unescape(match.group("url")).replace(r"\/", "/")
        if not is_trusted_https_url(target, cls.page_host_suffixes):
            return None
        return target if cls._product_id(target) is not None else None

    @classmethod
    def _extract_platform_metadata(
        cls,
        html_text: str,
        base_url: str,
    ) -> ProductMetadata:
        metadata = ProductMetadata()
        for payload in iter_json_script_values(html_text):
            for container in cls._iter_mappings(payload):
                item = container.get("item")
                if not isinstance(item, Mapping):
                    continue
                title = cls._scalar(item.get("title"))
                if not title:
                    continue
                price = container.get("price")
                seller = container.get("seller")
                price_mapping = price if isinstance(price, Mapping) else {}
                seller_mapping = seller if isinstance(seller, Mapping) else {}
                image_url = cls._first_image(item.get("images") or item.get("image"))
                candidate = ProductMetadata(
                    title=clean_product_text(title),
                    price=format_product_price(price_mapping.get("priceText")),
                    shop=clean_product_text(seller_mapping.get("shopName")),
                    image_url=urljoin(base_url, image_url) if image_url else "",
                )
                metadata = metadata.with_fallback(candidate)
        return metadata

    @classmethod
    def _iter_mappings(
        cls,
        value: object,
        depth: int = 0,
    ) -> Iterable[Mapping[str, object]]:
        if depth > 20:
            return
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from cls._iter_mappings(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                yield from cls._iter_mappings(child, depth + 1)

    @staticmethod
    def _scalar(value: object) -> str:
        return str(value) if isinstance(value, (str, int, float)) else ""

    @classmethod
    def _first_image(cls, value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return next(
                (
                    image.strip()
                    for image in value
                    if isinstance(image, str) and image.strip()
                ),
                "",
            )
        return ""

    def _build_result(
        self,
        metadata: ProductMetadata,
        canonical_url: str,
    ) -> ParseResult:
        extra_lines = []
        if metadata.price:
            extra_lines.append(f"价格: {metadata.price}")
        if metadata.shop:
            extra_lines.append(f"店铺: {metadata.shop}")
        extra_lines.append(f"商品链接: {canonical_url}")

        image_url = metadata.image_url
        if image_url and not is_trusted_https_url(
            image_url,
            self.image_host_suffixes,
        ):
            image_url = ""
        return ParseResult(
            platform=self.name,
            title=metadata.title,
            cover_urls=[image_url] if image_url else [],
            extra_lines=extra_lines,
        )
