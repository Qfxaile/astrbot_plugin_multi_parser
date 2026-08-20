"""解析 B站会员购公开商品与票务详情。"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import httpx

from ...core.contracts import ParseResult
from ...core.http import CookieAccessError, is_trusted_https_url
from ...core.product_metadata import clean_product_text, format_product_price
from .common import original_image_url

MallKind = Literal["ticket_new", "ticket_old", "product", "workshop", "market"]


@dataclass(frozen=True)
class MallTarget:
    """表示已通过域名、路径和 ID 校验的会员购详情链接。"""

    kind: MallKind
    item_id: str
    url: str


@dataclass(frozen=True)
class MallDetail:
    """表示不同会员购业务统一后的展示字段。"""

    title: str
    description: str = ""
    extra_lines: tuple[str, ...] = ()
    images: tuple[str, ...] = ()


class _MallHTMLParser(HTMLParser):
    """从公开详情 HTML 片段中提取摘要文字与图片。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.image_urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "img":
            return
        attributes = dict(attrs)
        image_url = str(attributes.get("data-src") or attributes.get("src") or "")
        if image_url:
            self.image_urls.append(image_url)

    def handle_data(self, data: str) -> None:
        if text := clean_product_text(data):
            self.text_parts.append(text)


URL_PATTERN = re.compile(
    r"https://[^\s<>\[\](){}，。！？、；：'\"`]+",
    re.IGNORECASE,
)
ITEM_ID_PATTERN = re.compile(r"\d{1,32}\Z")
GF_ITEM_PATTERN = re.compile(r"/item/detail/(?P<item_id>\d{1,32})/?\Z")
MALL_HOST = "mall.bilibili.com"
SHOW_HOST = "show.bilibili.com"
GF_HOST = "gf.bilibili.com"
TRUSTED_PAGE_HOSTS = ("bilibili.com",)
TRUSTED_IMAGE_HOSTS = ("hdslb.com",)
MAX_IMAGES = 6
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_READ_CHUNK_SIZE = 64 * 1024


class MallResponseTooLargeError(ValueError):
    """表示会员购接口响应超过允许读取的大小。"""


def find_mall_target(text: str) -> MallTarget | None:
    """从消息中查找首个受支持的会员购单品详情链接。"""
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,!?;:，。！？；：）】》")
        if target := _target_from_url(url):
            return target
    return None


def _target_from_url(url: str) -> MallTarget | None:
    if not is_trusted_https_url(url, TRUSTED_PAGE_HOSTS):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query, keep_blank_values=True)

    if host == GF_HOST and (match := GF_ITEM_PATTERN.fullmatch(parsed.path)):
        return MallTarget("workshop", match.group("item_id"), url)
    if host == SHOW_HOST and parsed.path == "/platform/detail.html":
        return _query_target("ticket_old", "id", query, url)
    if host != MALL_HOST:
        return None
    if parsed.path == "/neul-next/ticket-renovation/detail.html":
        return _query_target("ticket_new", "id", query, url)
    if parsed.path in {
        "/detail.html",
        "/neul-next/detailuniversal/detail.html",
    }:
        return _query_target("product", "itemsId", query, url)
    if parsed.path != "/neul-next/index.html":
        return None

    pages = query.get("page", [])
    if len(pages) != 1:
        return None
    kind_by_page: dict[str, MallKind] = {
        "detailuniversal_detail": "product",
        "mall-up_itemDetail": "workshop",
        "magic-market_detail": "market",
    }
    kind = kind_by_page.get(pages[0])
    return _query_target(kind, "itemsId", query, url) if kind else None


def _query_target(
    kind: MallKind,
    key: str,
    query: Mapping[str, list[str]],
    url: str,
) -> MallTarget | None:
    values = query.get(key, [])
    if len(values) != 1 or ITEM_ID_PATTERN.fullmatch(values[0]) is None:
        return None
    return MallTarget(kind, values[0], url)


def parse_ticket_detail(payload: object) -> MallDetail:
    """将新版或旧版会员购票务响应转换为统一详情。"""
    data = _payload_data(payload, "票务")
    basic = _mapping(data.get("basicInfoFloorVO"))
    detail_floor = _mapping(data.get("detailDescFloorVO"))
    venue = _mapping(
        _first_value(data, "skuVenueInfo", "venue_info") or basic.get("venueInfo")
    )
    merchant = _mapping(data.get("merchant"))

    title = _text(
        _first_value(data, "projectName", "project_name", "name")
        or basic.get("itemsName")
    )
    description = _summary(
        _first_value(basic, "brief", "extBrief") or data.get("description")
    )
    low_price = _first_value(
        data,
        "salablePriceLow",
        "salable_price_low",
        "priceLow",
        "price_low",
    )
    high_price = _first_value(
        data,
        "salablePriceHigh",
        "salable_price_high",
        "priceHigh",
        "price_high",
        default=low_price,
    )
    date_label = _text(
        _first_value(data, "projectLabel", "project_label", "showDate", "show_date")
    )
    organizer = _text(
        _first_value(merchant, "company", "name")
        or _first_value(data, "merchantName", "merchant_name")
    )
    venue_name = _text(_first_value(venue, "name", "venueName", "venue_name"))
    address = _join_text(
        _first_value(venue, "province_name", "provinceName"),
        _first_value(venue, "city_name", "cityName"),
        _first_value(venue, "district_name", "districtName"),
        _first_value(venue, "address_detail", "addressDetail"),
    )

    extra_lines = ["类型: 会员购票务"]
    if price := _cents_range(low_price, high_price, free=bool(data.get("isFree"))):
        extra_lines.append(f"票价: {price}")
    if date_label:
        extra_lines.append(f"活动日期: {date_label}")
    if organizer:
        extra_lines.append(f"主办方: {organizer}")
    if venue_name:
        extra_lines.append(f"场馆: {venue_name}")
    if address:
        extra_lines.append(f"地址: {address}")

    image_candidates: list[object] = []
    image_candidates.extend(_list_value(basic.get("imageList")))
    image_candidates.extend((data.get("banner"), data.get("cover")))
    image_candidates.extend(_performance_images(data.get("performance_image")))
    image_candidates.extend(_ticket_detail_images(detail_floor))
    return MallDetail(
        title=title or "B站会员购票务",
        description=description,
        extra_lines=tuple(extra_lines),
        images=tuple(_text(value) for value in image_candidates if _text(value)),
    )


def parse_product_detail(payload: object) -> MallDetail:
    """将普通或商家会员购商品响应转换为统一详情。"""
    data = _payload_data(payload, "商品")
    shop = _mapping(data.get("shopInfoVO"))
    if not shop:
        shop = _mapping(data.get("shopVO"))
    delivery = _mapping(data.get("itemsDeliveryInfo"))
    html_summary, html_images = _html_content(data.get("mobileDesc"))

    extra_lines = ["类型: 会员购商品"]
    if price := _yuan_range(data.get("price"), data.get("maxPrice")):
        extra_lines.append(f"价格: {price}")
    if shop_name := _text(_first_value(shop, "shopName", "name")):
        extra_lines.append(f"店铺: {shop_name}")
    if delivery_text := _text(
        _first_value(delivery, "deliveryMainDesc", "deliveryModeDesc")
    ):
        extra_lines.append(f"交付: {delivery_text}")
    extra_lines.extend(_attribute_lines(data.get("attrList")))

    images = [*_list_value(data.get("img")), *html_images]
    return MallDetail(
        title=_text(data.get("name")) or "B站会员购商品",
        description=_summary(_first_value(data, "brief", "extBrief") or html_summary),
        extra_lines=tuple(extra_lines),
        images=tuple(_text(value) for value in images if _text(value)),
    )


def parse_workshop_detail(payload: object) -> MallDetail:
    """将会员购工房商品响应转换为统一详情。"""
    data = _payload_data(payload, "工房商品")
    shop = _mapping(data.get("shopInfo"))
    discount = _mapping(data.get("itemsDiscountPriceVO"))
    tags = _mapping(data.get("itemsTag"))

    extra_lines = ["类型: 会员购工房"]
    if price := _cents_price(data.get("price")):
        extra_lines.append(f"价格: {price}")
    if fan_price := _cents_price(discount.get("discountPrice")):
        extra_lines.append(f"粉丝价: {fan_price}")
    if shop_name := _text(_first_value(shop, "shopUserNickName", "shopName", "name")):
        extra_lines.append(f"店铺: {shop_name}")
    delivery_tags = _unique_texts(tags.get("deliveryTagList"))
    if delivery_tags:
        extra_lines.append(f"交付: {' / '.join(delivery_tags)}")
    extra_lines.extend(_attribute_lines(data.get("itemsAttrs")))

    images = [*_list_value(data.get("mainImgList"))]
    for item in _list_value(data.get("detailImgList")):
        mapping = _mapping(item)
        images.append(mapping.get("url") if mapping else item)
    return MallDetail(
        title=_text(data.get("name")) or "B站会员购工房商品",
        description=_summary(data.get("description")),
        extra_lines=tuple(extra_lines),
        images=tuple(_text(value) for value in images if _text(value)),
    )


def parse_market_detail(payload: object) -> MallDetail:
    """将会员购魔力赏市集响应转换为统一详情。"""
    data = _payload_data(payload, "市集商品")
    item_details = [
        item
        for item in _list_value(data.get("detailDtoList"))
        if isinstance(item, Mapping)
    ]
    first_item = item_details[0] if item_details else {}
    title = _text(data.get("c2cItemsName") or first_item.get("name"))

    extra_lines = ["类型: 会员购市集"]
    price = format_product_price(data.get("showPrice"))
    if not price:
        price = _cents_price(data.get("price"))
    if price:
        extra_lines.append(f"价格: {price}")
    sale_status = {1: "在售", 2: "已成交"}.get(_integer(data.get("saleStatus")))
    if sale_status:
        extra_lines.append(f"状态: {sale_status}")
    if seller := _text(data.get("uname")):
        extra_lines.append(f"卖家: {seller}")
    item_names = [title, *(_text(item.get("name")) for item in item_details)]
    if any("福袋" in name for name in item_names if name):
        extra_lines.append("提示: 福袋商品内容具有不确定性")

    images = [_text(item.get("img")) for item in item_details]
    return MallDetail(
        title=title or "B站会员购市集商品",
        extra_lines=tuple(extra_lines),
        images=tuple(image for image in images if image),
    )


def mall_detail_result(detail: MallDetail) -> ParseResult:
    """限制并过滤图片后构造统一解析结果。"""
    images: list[str] = []
    for candidate in detail.images:
        image_url = original_image_url(candidate)
        if image_url not in images and is_trusted_https_url(
            image_url, TRUSTED_IMAGE_HOSTS
        ):
            images.append(image_url)
        if len(images) >= MAX_IMAGES:
            break
    return ParseResult(
        platform="bilibili",
        title=detail.title,
        description=detail.description,
        cover_urls=images[:1],
        image_urls=images[1:],
        extra_lines=list(detail.extra_lines),
    )


class BilibiliMallContent:
    """请求并解析 B站会员购各类公开单品详情。"""

    NEW_TICKET_API = "https://mall.bilibili.com/mall-search-items/items_detail/info"
    OLD_TICKET_API = "https://show.bilibili.com/api/ticket/project/getV2"
    PRODUCT_API = "https://mall.bilibili.com/mall-c-search/items/info"
    WORKSHOP_API = "https://mall.bilibili.com/mall-up-search/items/info"
    MARKET_API = (
        "https://mall.bilibili.com/mall-magic-c/internet/c2c/items/queryC2cItemsDetail"
    )

    async def _parse_mall(self, target: MallTarget) -> ParseResult:
        """请求目标详情接口，并在单张图片失败时保留其余摘要。"""
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                headers=self._headers(target.url),
                cookies=self._cookies(),
                **self.http_client_options,
            ) as client:
                payload = await self._request_mall_payload(client, target)
                self._raise_for_api_cookie_error(payload)
                detail = self._parse_mall_detail(target.kind, payload)
                result = mall_detail_result(detail)
                return await self.materialize_images(result, client, target.url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                return ParseResult(
                    platform="bilibili",
                    error="B站会员购内容已下架或不存在。",
                )
            return ParseResult(
                platform="bilibili",
                error="B站会员购请求失败，请稍后重试。",
            )
        except httpx.HTTPError:
            return ParseResult(
                platform="bilibili",
                error="B站会员购请求失败，请稍后重试。",
            )
        except MallResponseTooLargeError:
            return ParseResult(
                platform="bilibili",
                error="B站会员购响应过大，无法解析。",
            )
        except CookieAccessError:
            raise
        except ValueError:
            return ParseResult(
                platform="bilibili",
                error="B站会员购响应异常，无法解析。",
            )

    async def _request_mall_payload(
        self,
        client: httpx.AsyncClient,
        target: MallTarget,
    ) -> Mapping[str, object]:
        if target.kind == "ticket_new":
            payload = await self._request_json(
                client,
                "POST",
                self.NEW_TICKET_API,
                json={
                    "itemsId": int(target.item_id),
                    "itemsDetailPageType": 3,
                },
                headers={
                    "Origin": "https://mall.bilibili.com",
                    "Referer": target.url,
                },
            )
            self._raise_for_api_cookie_error(payload)
            if _payload_succeeded(payload):
                return payload
            return await self._request_legacy_ticket(client, target.item_id)
        if target.kind == "ticket_old":
            return await self._request_legacy_ticket(client, target.item_id)

        api, params = {
            "product": (self.PRODUCT_API, {"itemsId": target.item_id}),
            "workshop": (
                self.WORKSHOP_API,
                {"itemsId": target.item_id, "itemsPreviewId": "0"},
            ),
            "market": (
                self.MARKET_API,
                {"c2cItemsId": target.item_id, "csrf": ""},
            ),
        }[target.kind]
        return await self._request_json(client, "GET", api, params=params)

    async def _request_legacy_ticket(
        self,
        client: httpx.AsyncClient,
        item_id: str,
    ) -> Mapping[str, object]:
        return await self._request_json(
            client,
            "GET",
            self.OLD_TICKET_API,
            params={"version": "134", "id": item_id, "project_id": item_id},
        )

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: object,
    ) -> Mapping[str, object]:
        """流式读取会员购 JSON，并同时校验声明与实际响应大小。"""
        async with client.stream(
            method,
            url,
            follow_redirects=False,
            **kwargs,
        ) as response:
            self.raise_for_response_status(response)
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > MAX_RESPONSE_BYTES:
                    raise MallResponseTooLargeError

            chunks: list[bytes] = []
            received_size = 0
            async for chunk in response.aiter_bytes(chunk_size=_READ_CHUNK_SIZE):
                received_size += len(chunk)
                if received_size > MAX_RESPONSE_BYTES:
                    raise MallResponseTooLargeError
                chunks.append(chunk)
        return _json_mapping(b"".join(chunks))

    @staticmethod
    def _parse_mall_detail(kind: MallKind, payload: object) -> MallDetail:
        if kind in {"ticket_new", "ticket_old"}:
            return parse_ticket_detail(payload)
        if kind == "product":
            return parse_product_detail(payload)
        if kind == "workshop":
            return parse_workshop_detail(payload)
        return parse_market_detail(payload)


def _json_mapping(content: bytes) -> Mapping[str, object]:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
        raise ValueError("B站会员购响应格式错误") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("B站会员购响应格式错误")
    return payload


def _payload_succeeded(payload: Mapping[str, object]) -> bool:
    code = _first_value(payload, "code", "errno")
    return (
        payload.get("success") is not False
        and code in (None, 0)
        and isinstance(payload.get("data"), Mapping)
        and bool(payload.get("data"))
    )


def _payload_data(payload: object, content_name: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("B站会员购响应格式错误")
    code = _first_value(payload, "code", "errno")
    if payload.get("success") is False or code not in (None, 0):
        message = _text(_first_value(payload, "message", "msg"))
        raise ValueError(message or f"B站会员购{content_name}请求失败")
    data = payload.get("data")
    if not isinstance(data, Mapping) or not data:
        raise ValueError(f"B站会员购{content_name}已下架或不存在")
    return data


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _list_value(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _first_value(
    payload: Mapping[str, object],
    *keys: str,
    default: object = None,
) -> object:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return default


def _text(value: object) -> str:
    return clean_product_text(value)


def _summary(value: object, limit: int = 240) -> str:
    text = _text(value)
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _join_text(*values: object) -> str:
    return " ".join(_text(value) for value in values if _text(value))


def _integer(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _cents_price(value: object) -> str:
    cents = _integer(value)
    return f"¥{cents / 100:.2f}" if cents is not None and cents > 0 else ""


def _cents_range(low: object, high: object, *, free: bool = False) -> str:
    if free:
        return "免费"
    low_price = _cents_price(low)
    high_price = _cents_price(high)
    if not low_price:
        return high_price
    if not high_price or high_price == low_price:
        return low_price
    return f"{low_price} - {high_price}"


def _yuan_range(low: object, high: object) -> str:
    low_price = format_product_price(low)
    high_price = format_product_price(high)
    if not low_price:
        return high_price
    if not high_price or high_price == low_price:
        return low_price
    return f"{low_price} - {high_price}"


def _html_content(value: object) -> tuple[str, list[str]]:
    html_text = str(value or "")
    if not html_text:
        return "", []
    parser = _MallHTMLParser()
    parser.feed(html_text)
    parser.close()
    return _summary(" ".join(parser.text_parts)), parser.image_urls


def _performance_images(value: object) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        return []
    if not isinstance(payload, Mapping):
        return []
    images = []
    for key in ("banner", "first"):
        item = _mapping(payload.get(key))
        if image_url := _text(item.get("url")):
            images.append(image_url)
    return images


def _ticket_detail_images(detail_floor: Mapping[str, object]) -> list[str]:
    performance = _mapping(detail_floor.get("performanceDesc"))
    images: list[str] = []
    for module in _list_value(performance.get("list")):
        module_mapping = _mapping(module)
        details = module_mapping.get("details")
        if isinstance(details, str):
            _, module_images = _html_content(details)
            images.extend(module_images)
    return images


def _attribute_lines(value: object) -> list[str]:
    lines: list[str] = []
    for item in _list_value(value):
        mapping = _mapping(item)
        name = _text(_first_value(mapping, "attrName", "name"))
        raw_values = _first_value(mapping, "attrValue", "values", "value")
        values = _unique_texts(raw_values)
        if name and values:
            lines.append(f"{name}: {' / '.join(values)}")
        if len(lines) >= 3:
            break
    return lines


def _unique_texts(value: object) -> list[str]:
    values: Iterable[object]
    if isinstance(value, (list, tuple)):
        values = value
    elif value in (None, ""):
        values = ()
    else:
        values = (value,)
    result: list[str] = []
    for item in values:
        text = _text(item)
        if text and text not in result:
            result.append(text)
    return result
