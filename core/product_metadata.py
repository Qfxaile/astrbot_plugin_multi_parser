"""提取公开商品页中的标准结构化元数据。"""

import html
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin


@dataclass(frozen=True)
class ProductMetadata:
    """表示商品卡片所需的可选公开字段。"""

    title: str = ""
    price: str = ""
    shop: str = ""
    image_url: str = ""

    def with_fallback(self, fallback: "ProductMetadata") -> "ProductMetadata":
        """仅使用后备值补齐当前缺失字段。"""
        return ProductMetadata(
            title=self.title or fallback.title,
            price=self.price or fallback.price,
            shop=self.shop or fallback.shop,
            image_url=self.image_url or fallback.image_url,
        )


def clean_product_text(value: object) -> str:
    """解码 HTML 实体并折叠商品字段中的多余空白。"""
    return " ".join(html.unescape(str(value or "")).split())


def format_product_price(value: object, currency: object = "CNY") -> str:
    """把公开价格格式化为稳定且不推算优惠的显示文本。"""
    amount = clean_product_text(value)
    if not amount:
        return ""
    unit = clean_product_text(currency).upper()
    if unit in {"", "CNY", "RMB", "¥", "￥"}:
        return amount if amount.startswith(("¥", "￥")) else f"¥{amount}"
    return f"{unit} {amount}"


class _ProductMetadataHTMLParser(HTMLParser):
    """收集 JSON 脚本与 OpenGraph 元标签，不执行页面脚本。"""

    def __init__(self) -> None:
        super().__init__()
        self.json_ld_texts: list[str] = []
        self.json_texts: list[str] = []
        self.metadata: dict[str, str] = {}
        self._script_type = ""
        self._script_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "meta":
            key = (attributes.get("property") or attributes.get("name")).lower()
            content = attributes.get("content", "").strip()
            if key and content and key not in self.metadata:
                self.metadata[key] = content
            return

        if tag.lower() == "script":
            self._script_type = (
                attributes.get("type", "").split(";", 1)[0].strip().lower()
            )
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_type:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._script_type:
            return
        text = "".join(self._script_chunks).strip()
        if text:
            if self._script_type == "application/ld+json":
                self.json_ld_texts.append(text)
            elif self._script_type == "application/json":
                self.json_texts.append(text)
        self._script_type = ""
        self._script_chunks = []


def _parse_html(html_text: str) -> _ProductMetadataHTMLParser:
    parser = _ProductMetadataHTMLParser()
    parser.feed(html_text)
    parser.close()
    return parser


def _decode_json_values(texts: Iterable[str]) -> Iterable[object]:
    for text in texts:
        try:
            yield json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            continue


def iter_json_script_values(html_text: str) -> Iterable[object]:
    """枚举页面中可独立解码的普通 JSON 脚本值。"""
    return _decode_json_values(_parse_html(html_text).json_texts)


def _iter_json_ld_nodes(value: object) -> Iterable[Mapping[str, object]]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_json_ld_nodes(item)
        return
    if not isinstance(value, Mapping):
        return

    graph = value.get("@graph")
    if isinstance(graph, (list, Mapping)):
        yield from _iter_json_ld_nodes(graph)
    yield value


def _is_product_node(node: Mapping[str, object]) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, str):
        return node_type.lower() == "product"
    if isinstance(node_type, list):
        return any(
            isinstance(value, str) and value.lower() == "product" for value in node_type
        )
    return False


def _first_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, Mapping)), {})
    return {}


def _image_value(value: object) -> str:
    if isinstance(value, str):
        return clean_product_text(value)
    if isinstance(value, list):
        for item in value:
            if image_url := _image_value(item):
                return image_url
        return ""
    if isinstance(value, Mapping):
        return _image_value(value.get("url") or value.get("contentUrl"))
    return ""


def _metadata_from_json_ld_node(
    node: Mapping[str, object],
    base_url: str,
) -> ProductMetadata:
    offers = _first_mapping(node.get("offers"))
    seller = _first_mapping(node.get("seller"))
    image_url = _image_value(node.get("image"))
    return ProductMetadata(
        title=clean_product_text(node.get("name")),
        price=format_product_price(
            offers.get("price") or offers.get("lowPrice"),
            offers.get("priceCurrency", "CNY"),
        ),
        shop=clean_product_text(seller.get("name")),
        image_url=urljoin(base_url, image_url) if image_url else "",
    )


def extract_json_ld_product(html_text: str, base_url: str) -> ProductMetadata:
    """按文档顺序合并 JSON-LD Product 节点中的非空字段。"""
    metadata = ProductMetadata()
    parser = _parse_html(html_text)
    for payload in _decode_json_values(parser.json_ld_texts):
        for node in _iter_json_ld_nodes(payload):
            if _is_product_node(node):
                metadata = metadata.with_fallback(
                    _metadata_from_json_ld_node(node, base_url)
                )
    return metadata


def extract_open_graph_product(html_text: str, base_url: str) -> ProductMetadata:
    """提取 OpenGraph 商品卡片字段作为最后降级来源。"""
    metadata = _parse_html(html_text).metadata
    image_url = clean_product_text(metadata.get("og:image"))
    return ProductMetadata(
        title=clean_product_text(metadata.get("og:title")),
        price=format_product_price(
            metadata.get("product:price:amount"),
            metadata.get("product:price:currency", "CNY"),
        ),
        image_url=urljoin(base_url, image_url) if image_url else "",
    )
