import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.webpage import FetchedWebPage, TrustedWebPageError
from astrbot_multi_parser.platforms.taobao import TaobaoParser


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://item.taobao.com/item.htm?id=123456", True),
        ("https://detail.tmall.com/item.htm?id=234567", True),
        ("https://h5.m.taobao.com/awp/core/detail.htm?id=345678", True),
        ("https://m.tb.cn/h.Abc123", True),
        ("https://e.tb.cn/h.Abc123", True),
        ("http://item.taobao.com/item.htm?id=123456", False),
        ("https://item.taobao.com.evil.test/item.htm?id=123456", False),
        ("https://user@item.taobao.com/item.htm?id=123456", False),
        ("https://item.taobao.com:444/item.htm?id=123456", False),
        ("https://item.taobao.com/search.htm?q=test", False),
    ],
)
async def test_taobao_match_accepts_only_supported_product_urls(url, expected):
    assert await TaobaoParser({}).match(ParseContext(text=url)) is expected


def test_taobao_builds_clean_canonical_urls():
    parser = TaobaoParser({})

    assert (
        parser._canonical_product_url(
            "https://item.taobao.com/item.htm?id=123456&spm=secret"
        )
        == "https://item.taobao.com/item.htm?id=123456"
    )
    assert (
        parser._canonical_product_url(
            "https://detail.tmall.com/item.htm?id=234567&ali_trackid=secret"
        )
        == "https://detail.tmall.com/item.htm?id=234567"
    )
    assert (
        parser._canonical_product_url(
            "https://h5.m.taobao.com/awp/core/detail.htm?id=345678"
        )
        == "https://item.taobao.com/item.htm?id=345678"
    )


async def test_taobao_parse_uses_json_ld_then_platform_data(monkeypatch):
    parser = TaobaoParser({})
    requested = []
    materialized = []
    html = """
    <script type="application/ld+json">
      {"@type":"Product","name":"JSON-LD标题",
       "offers":{"price":"299.00","priceCurrency":"CNY"}}
    </script>
    <script type="application/json">
      {"item":{"title":"内嵌标题",
               "images":["https://img.alicdn.com/main.jpg"]},
       "price":{"priceText":"199.00"},
       "seller":{"shopName":"淘宝测试店"}}
    </script>
    <meta property="og:title" content="OG标题">
    """

    async def fetch_page(client, url, host_suffixes):
        requested.append((url, host_suffixes))
        return FetchedWebPage(
            "https://item.taobao.com/item.htm?id=123456&spm=secret",
            html,
        )

    async def materialize(result, client, referer):
        materialized.append(referer)
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(ParseContext(text="分享 https://m.tb.cn/h.Abc123"))

    assert result.title == "JSON-LD标题"
    assert result.cover_urls == ["https://img.alicdn.com/main.jpg"]
    assert result.extra_lines == [
        "价格: ¥299.00",
        "店铺: 淘宝测试店",
        "商品链接: https://item.taobao.com/item.htm?id=123456",
    ]
    assert requested[0][0] == "https://m.tb.cn/h.Abc123"
    assert materialized == ["https://item.taobao.com/item.htm?id=123456&spm=secret"]


async def test_taobao_parse_falls_back_to_open_graph_without_price(monkeypatch):
    parser = TaobaoParser({})
    html = """
    <meta property="og:title" content="公开商品">
    <meta property="og:image" content="https://img.alicdn.com/og.jpg">
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://detail.tmall.com/item.htm?id=234567",
            html,
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(
        ParseContext(text="https://detail.tmall.com/item.htm?id=234567")
    )

    assert result.title == "公开商品"
    assert result.extra_lines == [
        "商品链接: https://detail.tmall.com/item.htm?id=234567"
    ]


async def test_taobao_parse_rejects_short_link_to_non_product_page(monkeypatch):
    parser = TaobaoParser({})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage("https://www.taobao.com/", "<html></html>")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(ParseContext(text="https://m.tb.cn/h.Abc123"))

    assert result.error == "淘宝/天猫分享链接未指向受支持的商品。"


@pytest.mark.parametrize("marker", ["验证码", "安全验证", "登录后查看"])
async def test_taobao_parse_reports_verification_page(monkeypatch, marker):
    parser = TaobaoParser({})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.taobao.com/item.htm?id=123456",
            f"<html>{marker}</html>",
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(
        ParseContext(text="https://item.taobao.com/item.htm?id=123456")
    )

    assert result.error == "淘宝/天猫商品页面需要验证或登录，暂时无法匿名解析。"


async def test_taobao_parse_does_not_leak_network_error(monkeypatch):
    parser = TaobaoParser({})

    async def fetch_page(client, url, host_suffixes):
        raise httpx.ConnectError("https://m.tb.cn/h.private-token")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(ParseContext(text="https://m.tb.cn/h.Abc123"))

    assert result.error == "淘宝/天猫商品请求失败，请稍后重试。"
    assert "private-token" not in result.error


async def test_taobao_parse_keeps_trusted_page_error_without_target(monkeypatch):
    parser = TaobaoParser({})

    async def fetch_page(client, url, host_suffixes):
        raise TrustedWebPageError("商品分享链接跳转到不可信域名。")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(ParseContext(text="https://m.tb.cn/h.Abc123"))

    assert result.error == "商品分享链接跳转到不可信域名。"
