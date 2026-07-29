"""解析拼多多匿名可访问的公开商品页。"""

import re
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qs, urljoin, urlsplit

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


class PinduoduoParser(BaseParser):
    """解析匿名可访问的拼多多公开商品页。"""

    name = "pinduoduo"
    display_name = "拼多多"
    page_host_suffixes = ("yangkeduo.com", "pinduoduo.com")
    image_host_suffixes = ("pddpic.com", "yangkeduo.com")
    URL_PATTERN = re.compile(
        r"https://[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    GOODS_ID_PATTERN = re.compile(r"\d{1,32}\Z")
    SHARE_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
    PRODUCT_PATHS = frozenset({"/goods.html", "/goods2.html"})
    NEED_LOGIN_PATTERN = re.compile(r'"needLogin"\s*:\s*true\b')
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
            return ParseResult(platform=self.name, error="未找到拼多多商品链接。")

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
                if not self._is_goods_page(page.final_url):
                    return ParseResult(
                        platform=self.name,
                        error="拼多多分享链接未指向受支持的商品。",
                    )
                if any(
                    marker in page.html for marker in self.VERIFY_MARKERS
                ) or self.NEED_LOGIN_PATTERN.search(page.html):
                    return ParseResult(
                        platform=self.name,
                        error="拼多多商品页面需要验证或登录，暂时无法匿名解析。",
                    )

                platform_metadata, goods_id = self._extract_platform_metadata(
                    page.html,
                    page.final_url,
                )
                canonical_url = self._canonical_product_url(
                    page.final_url,
                    goods_id=goods_id,
                )
                if not canonical_url:
                    return ParseResult(
                        platform=self.name,
                        error="拼多多分享链接未指向受支持的商品。",
                    )

                metadata = extract_json_ld_product(
                    page.html,
                    page.final_url,
                )
                metadata = metadata.with_fallback(platform_metadata)
                metadata = metadata.with_fallback(
                    extract_open_graph_product(page.html, page.final_url)
                )
                if not metadata.title:
                    return ParseResult(
                        platform=self.name,
                        error=("未找到拼多多商品信息，页面可能需要登录或结构已变化。"),
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
                    error="该拼多多商品已下架或不存在。",
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
        if host == "p.pinduoduo.com":
            return parsed.path not in {"", "/"}
        return cls._is_goods_page(url)

    @classmethod
    def _is_goods_page(cls, url: str) -> bool:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        if (
            parsed.hostname or ""
        ).lower() != "mobile.yangkeduo.com" or parsed.path not in cls.PRODUCT_PATHS:
            return False
        goods_id, share_code = cls._query_locators(url)
        return bool(goods_id or share_code)

    @classmethod
    def _query_locators(cls, url: str) -> tuple[str, str]:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        goods_ids = query.get("goods_id", [])
        share_codes = query.get("ps", [])
        goods_id = (
            goods_ids[0]
            if len(goods_ids) == 1 and cls.GOODS_ID_PATTERN.fullmatch(goods_ids[0])
            else ""
        )
        share_code = (
            share_codes[0]
            if len(share_codes) == 1
            and cls.SHARE_CODE_PATTERN.fullmatch(share_codes[0])
            else ""
        )
        return goods_id, share_code

    @classmethod
    def _canonical_product_url(
        cls,
        url: str,
        *,
        goods_id: str = "",
    ) -> str | None:
        if not cls._is_goods_page(url):
            return None
        url_goods_id, share_code = cls._query_locators(url)
        selected_goods_id = url_goods_id
        if not selected_goods_id and cls.GOODS_ID_PATTERN.fullmatch(goods_id):
            selected_goods_id = goods_id
        if selected_goods_id:
            return (
                f"https://mobile.yangkeduo.com/goods.html?goods_id={selected_goods_id}"
            )
        if share_code:
            return f"https://mobile.yangkeduo.com/goods.html?ps={share_code}"
        return None

    @classmethod
    def _extract_platform_metadata(
        cls,
        html_text: str,
        base_url: str,
    ) -> tuple[ProductMetadata, str]:
        metadata = ProductMetadata()
        goods_id = ""
        for payload in iter_json_script_values(html_text):
            for container in cls._iter_mappings(payload):
                goods = container.get("goods")
                if not isinstance(goods, Mapping):
                    continue
                title = cls._scalar(goods.get("goodsName"))
                candidate_goods_id = cls._scalar(goods.get("goodsId"))
                if cls.GOODS_ID_PATTERN.fullmatch(candidate_goods_id):
                    goods_id = goods_id or candidate_goods_id
                if not title:
                    continue
                image_url = cls._scalar(goods.get("hdThumbUrl"))
                candidate = ProductMetadata(
                    title=clean_product_text(title),
                    price=cls._format_platform_price(goods.get("minGroupPrice")),
                    shop=clean_product_text(goods.get("mallName")),
                    image_url=urljoin(base_url, image_url) if image_url else "",
                )
                metadata = metadata.with_fallback(candidate)
        return metadata, goods_id

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

    @staticmethod
    def _format_platform_price(value: object) -> str:
        if isinstance(value, bool):
            return ""
        if isinstance(value, int):
            if value < 0:
                return ""
            return format_product_price(f"{value // 100}.{value % 100:02d}")
        if isinstance(value, str):
            return format_product_price(value)
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

    def _network_error(self) -> ParseResult:
        return ParseResult(
            platform=self.name,
            error="拼多多商品请求失败，请稍后重试。",
        )
