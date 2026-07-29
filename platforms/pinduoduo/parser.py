"""解析拼多多匿名可访问的公开商品页。"""

import json
import re
import time
from collections.abc import Iterable, Mapping
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import (
    CookieAccessError,
    build_cookies,
    cookie_config_value,
    is_trusted_https_url,
)
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
    cookie_config_key = "pinduoduo_cookies"
    cookie_domains = (".yangkeduo.com", ".pinduoduo.com")
    page_host_suffixes = ("yangkeduo.com", "pinduoduo.com")
    image_host_suffixes = ("pddpic.com", "yangkeduo.com")
    URL_PATTERN = re.compile(
        r"https://[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    GOODS_ID_PATTERN = re.compile(r"\d{1,32}\Z")
    SHARE_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
    RAW_DATA_PATTERN = re.compile(r"\bwindow\.rawData\s*=\s*")
    PRODUCT_PATHS = frozenset({"/goods.html", "/goods2.html"})
    GENERIC_TITLES = frozenset({"拼多多", "拼多多商城"})
    NEED_LOGIN_PATTERN = re.compile(r'"needLogin"\s*:\s*true\b')
    OAK_ENDPOINT = "https://mobile.yangkeduo.com/proxy/api/api/oak/integration/render"
    OAK_MAX_BYTES = 2 * 1024 * 1024
    OAK_FRONT_SUPPORTS = (
        "community_purchase",
        "split_info_section",
        "render_opt_2022",
        "new_price_bottom",
        "custom_sku",
    )
    PRODUCT_IMAGE_KEYS = (
        "hd_thumb_url",
        "hdThumbUrl",
        "thumb_url",
        "thumbUrl",
    )
    PRODUCT_FALLBACK_IMAGE_KEYS = (
        "goods_image_url",
        "goodsImageUrl",
    )
    PRODUCT_GALLERY_KEYS = ("gallery", "goods_gallery", "goodsGallery")
    GALLERY_IMAGE_KEYS = ("url", "img_url", "imgUrl", "image_url", "imageUrl")
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
                        error=str(self.cookie_access_error()),
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
                if metadata.title in self.GENERIC_TITLES:
                    metadata = ProductMetadata(
                        price=metadata.price,
                        shop=metadata.shop,
                    )
                elif metadata.image_url and self._is_generic_product_image(
                    metadata.image_url
                ):
                    metadata = ProductMetadata(
                        title=metadata.title,
                        price=metadata.price,
                        shop=metadata.shop,
                    )
                if not metadata.title or not metadata.image_url:
                    url_goods_id, _ = self._query_locators(page.final_url)
                    oak_goods_id = url_goods_id or goods_id
                    if oak_goods_id and cookie_config_value(
                        self.config,
                        self.cookie_config_key,
                    ):
                        oak_metadata, oak_goods_id = await self._fetch_oak_metadata(
                            client,
                            oak_goods_id,
                        )
                        metadata = oak_metadata.with_fallback(metadata)
                        canonical_url = self._canonical_product_url(
                            page.final_url,
                            goods_id=oak_goods_id,
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
        except CookieAccessError as exc:
            return ParseResult(platform=self.name, error=str(exc))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in self.cookie_failure_status_codes:
                return ParseResult(
                    platform=self.name,
                    error=str(self.cookie_access_error()),
                )
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
        for payload in cls._iter_platform_payloads(html_text):
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
                image_url = cls._first_product_image(goods)
                candidate = ProductMetadata(
                    title=clean_product_text(title),
                    price=cls._format_platform_price(goods.get("minGroupPrice")),
                    shop=clean_product_text(goods.get("mallName")),
                    image_url=urljoin(base_url, image_url) if image_url else "",
                )
                metadata = metadata.with_fallback(candidate)
        return metadata, goods_id

    @classmethod
    def _extract_oak_metadata(
        cls,
        payload: Mapping[str, object],
        base_url: str,
    ) -> ProductMetadata:
        goods = payload.get("goods")
        if not isinstance(goods, Mapping):
            return ProductMetadata()

        mall_name = goods.get("mall_name") or goods.get("mallName")
        mall_entrance = payload.get("mall_entrance")
        if isinstance(mall_entrance, Mapping):
            mall_data = mall_entrance.get("mall_data")
            if isinstance(mall_data, Mapping):
                mall_name = mall_data.get("mall_name") or mall_data.get("mallName")

        title = cls._scalar(goods.get("goods_name") or goods.get("goodsName"))
        image_url = cls._first_product_image(goods)
        return ProductMetadata(
            title=clean_product_text(title),
            price=cls._format_platform_price(cls._oak_price_value(payload, goods)),
            shop=clean_product_text(cls._scalar(mall_name)),
            image_url=urljoin(base_url, image_url) if image_url else "",
        )

    @staticmethod
    def _oak_price_value(
        payload: Mapping[str, object],
        goods: Mapping[str, object],
    ) -> object:
        for key in ("min_group_price", "minGroupPrice"):
            value = goods.get(key)
            if value is not None:
                return value

        price = payload.get("price")
        if not isinstance(price, Mapping):
            return price
        for key in (
            "min_group_price",
            "minGroupPrice",
            "group_price",
            "groupPrice",
            "price",
        ):
            value = price.get(key)
            if value is not None:
                return value
        return None

    async def _fetch_oak_metadata(
        self,
        client: httpx.AsyncClient,
        goods_id: str,
    ) -> tuple[ProductMetadata, str]:
        """使用当前拼多多 Cookie 会话读取固定商品的详情数据。"""
        if self.GOODS_ID_PATTERN.fullmatch(goods_id) is None:
            raise self.cookie_access_error()

        timestamp = int(time.time() * 1000)
        canonical_url = f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"
        request_payload = {
            "page_version": 7,
            "goods_id": goods_id,
            "page_from": 0,
            "hostname": "mobile.yangkeduo.com",
            "client_time": timestamp,
            "page_sn": 10014,
            "page_id": f"10014_{timestamp}_0",
            "front_supports": list(self.OAK_FRONT_SUPPORTS),
        }
        async with client.stream(
            "POST",
            self.OAK_ENDPOINT,
            json=request_payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://mobile.yangkeduo.com",
                "Referer": canonical_url,
            },
            follow_redirects=False,
        ) as response:
            self.raise_for_response_status(response)
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > self.OAK_MAX_BYTES:
                    raise self.cookie_access_error()

            chunks: list[bytes] = []
            received_size = 0
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                received_size += len(chunk)
                if received_size > self.OAK_MAX_BYTES:
                    raise self.cookie_access_error()
                chunks.append(chunk)

        try:
            payload = json.loads(b"".join(chunks))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise self.cookie_access_error() from None
        if not isinstance(payload, Mapping):
            raise self.cookie_access_error()
        if payload.get("error_code") or payload.get("encrypt_status") == 1:
            raise self.cookie_access_error()

        metadata = self._extract_oak_metadata(payload, canonical_url)
        goods = payload.get("goods")
        response_goods_id = ""
        if isinstance(goods, Mapping):
            response_goods_id = self._scalar(
                goods.get("goods_id") or goods.get("goodsId")
            )
        if (
            not metadata.title
            or self.GOODS_ID_PATTERN.fullmatch(response_goods_id) is None
            or response_goods_id != goods_id
        ):
            raise self.cookie_access_error()
        return metadata, response_goods_id

    @classmethod
    def _iter_platform_payloads(cls, html_text: str) -> Iterable[object]:
        yield from iter_json_script_values(html_text)
        match = cls.RAW_DATA_PATTERN.search(html_text)
        if match is None:
            return
        try:
            payload, _ = json.JSONDecoder().raw_decode(html_text, match.end())
        except (json.JSONDecodeError, RecursionError):
            return
        yield payload

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
    def _first_product_image(cls, goods: Mapping[str, object]) -> str:
        for key in cls.PRODUCT_IMAGE_KEYS:
            image_url = cls._scalar(goods.get(key)).strip()
            if image_url and not cls._is_generic_product_image(image_url):
                return image_url

        for key in cls.PRODUCT_GALLERY_KEYS:
            gallery = goods.get(key)
            if isinstance(gallery, Mapping):
                gallery_items: Iterable[object] = (gallery,)
            elif isinstance(gallery, list):
                gallery_items = gallery
            else:
                continue
            for item in gallery_items:
                if isinstance(item, str):
                    image_url = item.strip()
                    if image_url and not cls._is_generic_product_image(image_url):
                        return image_url
                    continue
                if not isinstance(item, Mapping):
                    continue
                for image_key in cls.GALLERY_IMAGE_KEYS:
                    image_url = cls._scalar(item.get(image_key)).strip()
                    if image_url and not cls._is_generic_product_image(image_url):
                        return image_url

        for key in cls.PRODUCT_FALLBACK_IMAGE_KEYS:
            image_url = cls._scalar(goods.get(key)).strip()
            if image_url and not cls._is_generic_product_image(image_url):
                return image_url
        return ""

    @staticmethod
    def _is_generic_product_image(image_url: str) -> bool:
        try:
            filename = urlsplit(image_url).path.rsplit("/", 1)[-1].lower()
        except ValueError:
            return True
        return filename in {"share_logo.jpg", "share_logo.png", "share_logo.webp"}

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
        _canonical_url: str,
    ) -> ParseResult:
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
            extra_lines=[],
        )

    def _network_error(self) -> ParseResult:
        return ParseResult(
            platform=self.name,
            error="拼多多商品请求失败，请稍后重试。",
        )
