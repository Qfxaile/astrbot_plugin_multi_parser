"""解析京东匿名可访问的公开商品页。"""

import json
import re
from collections.abc import Iterable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
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


class JDParser(BaseParser):
    """解析匿名可访问的京东公开商品页。"""

    name = "jd"
    display_name = "京东"
    page_host_suffixes = ("jd.com", "3.cn")
    image_host_suffixes = ("360buyimg.com", "jd.com")
    URL_PATTERN = re.compile(
        r"https://[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    SKU_PATTERN = re.compile(r"\d{1,32}\Z")
    DESKTOP_PATH_PATTERN = re.compile(r"/(?P<sku>\d{1,32})\.html\Z")
    MOBILE_PATH_PATTERN = re.compile(r"/product/(?P<sku>\d{1,32})\.html\Z")
    ITEM_INFO_PATTERN = re.compile(r"window\._itemInfo\s*=\s*(?:\(\s*)?\{")
    SHORT_HOSTS = frozenset({"3.cn", "u.jd.com"})
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
            return ParseResult(platform=self.name, error="未找到京东商品链接。")

        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
            ) as client:
                page = await fetch_trusted_html(
                    client,
                    url,
                    self.page_host_suffixes,
                )
                canonical_url = self._canonical_product_url(page.final_url)
                if not canonical_url:
                    return ParseResult(
                        platform=self.name,
                        error="京东分享链接未指向受支持的商品。",
                    )
                if any(marker in page.html for marker in self.VERIFY_MARKERS):
                    return ParseResult(
                        platform=self.name,
                        error="京东商品页面需要验证或登录，暂时无法匿名解析。",
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
                        error=("未找到京东商品信息，页面可能需要登录或结构已变化。"),
                    )

                result = self._build_result(metadata, canonical_url)
                if not result.cover_urls:
                    return result
                return await self.materialize_images(result, client, page.final_url)
        except TrustedWebPageError as exc:
            return ParseResult(platform=self.name, error=str(exc))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                return ParseResult(
                    platform=self.name,
                    error="该京东商品已下架或不存在。",
                )
            return self._network_error()
        except httpx.HTTPError:
            return self._network_error()

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
        return cls._sku_from_url(url) is not None

    @classmethod
    def _sku_from_url(cls, url: str) -> str | None:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
        pattern = None
        if host == "item.jd.com":
            pattern = cls.DESKTOP_PATH_PATTERN
        elif host == "item.m.jd.com":
            pattern = cls.MOBILE_PATH_PATTERN
        if pattern is None or (match := pattern.fullmatch(parsed.path)) is None:
            return None
        sku = match.group("sku")
        return sku if cls.SKU_PATTERN.fullmatch(sku) else None

    @classmethod
    def _canonical_product_url(cls, url: str) -> str | None:
        sku = cls._sku_from_url(url)
        return f"https://item.jd.com/{sku}.html" if sku else None

    @classmethod
    def _extract_platform_metadata(
        cls,
        html_text: str,
        base_url: str,
    ) -> ProductMetadata:
        product = cls._item_info_mapping(html_text, "product")
        stock = cls._item_info_mapping(html_text, "stock")
        shop = stock.get("D") if isinstance(stock, Mapping) else None
        shop_mapping = shop if isinstance(shop, Mapping) else {}
        image_url = cls._scalar(product.get("imageurl") or product.get("imageUrl"))
        metadata = ProductMetadata(
            title=clean_product_text(product.get("skuName") or product.get("name")),
            shop=clean_product_text(shop_mapping.get("shopName")),
            image_url=cls._normalize_image_url(base_url, image_url),
        )
        for payload in iter_json_script_values(html_text):
            for container in cls._iter_mappings(payload):
                product = container.get("product")
                if not isinstance(product, Mapping):
                    continue
                title = cls._scalar(product.get("skuName") or product.get("name"))
                if not title:
                    continue
                price_value = product.get("price")
                if isinstance(price_value, Mapping):
                    price_value = price_value.get("price")
                image_url = cls._scalar(product.get("image") or product.get("imageUrl"))
                candidate = ProductMetadata(
                    title=clean_product_text(title),
                    price=format_product_price(price_value),
                    shop=clean_product_text(product.get("shopName")),
                    image_url=cls._normalize_image_url(base_url, image_url),
                )
                metadata = metadata.with_fallback(candidate)
        return metadata

    @classmethod
    def _item_info_mapping(
        cls,
        html_text: str,
        key: str,
    ) -> Mapping[str, object]:
        assignment = cls.ITEM_INFO_PATTERN.search(html_text)
        if assignment is None:
            return {}
        script_end = html_text.find("</script>", assignment.end())
        if script_end < 0:
            return {}
        script = html_text[assignment.start() : script_end]
        marker = re.search(rf'"{re.escape(key)}"\s*:\s*', script)
        if marker is None:
            return {}
        try:
            value, _ = json.JSONDecoder().raw_decode(script, marker.end())
        except (json.JSONDecodeError, RecursionError):
            return {}
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _normalize_image_url(base_url: str, image_url: str) -> str:
        if not image_url:
            return ""
        if image_url.startswith("jfs/"):
            return f"https://img10.360buyimg.com/n1/{image_url}"
        return urljoin(base_url, image_url)

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

    def _network_error(self) -> ParseResult:
        return ParseResult(
            platform=self.name,
            error="京东商品请求失败，请稍后重试。",
        )
