import hashlib
import json

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.http import CookieAccessError, build_cookies
from astrbot_multi_parser.core.product_metadata import ProductMetadata
from astrbot_multi_parser.core.webpage import FetchedWebPage, TrustedWebPageError
from astrbot_multi_parser.platforms.taobao import TaobaoParser


def test_taobao_result_only_displays_product_content():
    result = TaobaoParser({})._build_result(
        ProductMetadata(title="淘宝商品", price="¥88.00", shop="淘宝店铺"),
        "https://item.taobao.com/item.htm?id=123456",
    )

    assert result.extra_lines == []


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
    assert result.extra_lines == []
    assert requested[0][0] == "https://m.tb.cn/h.Abc123"
    assert materialized == ["https://item.taobao.com/item.htm?id=123456&spm=secret"]


async def test_taobao_parse_follows_client_side_share_target(monkeypatch):
    parser = TaobaoParser({})
    requested = []
    share_html = """
    <script>
      var url = 'https://item.taobao.com/item.htm?id=1067554939784&spm=secret';
      window.location.replace(url);
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        requested.append(url)
        if url.startswith("https://e.tb.cn/"):
            return FetchedWebPage(
                "https://e.tb.cn/h.8VdXPOwpkmPwjZu?tk=share",
                share_html,
            )
        return FetchedWebPage(
            "https://h5.m.taobao.com/awp/core/detail.htm?id=1067554939784",
            '<meta property="og:title" content="淘宝公开商品">',
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(
        ParseContext(text="https://e.tb.cn/h.8VdXPOwpkmPwjZu?tk=share")
    )

    assert result.title == "淘宝公开商品"
    assert result.extra_lines == []
    assert requested == [
        "https://e.tb.cn/h.8VdXPOwpkmPwjZu?tk=share",
        "https://item.taobao.com/item.htm?id=1067554939784&spm=secret",
    ]


async def test_taobao_parse_uses_mtop_when_page_has_no_metadata(monkeypatch):
    parser = TaobaoParser(
        {
            "cookies": {
                "taobao_cookies": "_m_h5_tk=test-token_123; _m_h5_tk_enc=test-enc"
            }
        }
    )
    requested_item_ids = []

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://h5.m.taobao.com/awp/core/detail.htm?id=1067554939784",
            "<html></html>",
        )

    async def fetch_api_metadata(client, item_id):
        requested_item_ids.append(item_id)
        return ProductMetadata(
            title="MTop 淘宝商品",
            price="¥17.52",
            shop="MTop 店铺",
            image_url="https://img.alicdn.com/product.jpg",
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )
    monkeypatch.setattr(
        parser,
        "_fetch_api_metadata",
        fetch_api_metadata,
        raising=False,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(
        ParseContext(text="https://item.taobao.com/item.htm?id=1067554939784")
    )

    assert result.title == "MTop 淘宝商品"
    assert result.cover_urls == ["https://img.alicdn.com/product.jpg"]
    assert result.extra_lines == []
    assert requested_item_ids == ["1067554939784"]


async def test_taobao_mtop_request_is_signed_and_extracts_product():
    cookie_value = "_m_h5_tk=test-token_123; _m_h5_tk_enc=test-enc"
    parser = TaobaoParser({"cookies": {"taobao_cookies": cookie_value}})
    assert hasattr(parser, "_fetch_api_metadata"), "淘宝解析器尚未实现 MTop 详情请求"

    def handler(request):
        assert request.url.host == "h5api.m.taobao.com"
        assert request.url.path == "/h5/mtop.taobao.pcdetail.data.get/1.0/"
        params = request.url.params
        assert params["api"] == "mtop.taobao.pcdetail.data.get"
        request_data = params["data"]
        assert json.loads(request_data)["id"] == "1067554939784"
        expected_sign = hashlib.md5(
            f"test-token&{params['t']}&12574478&{request_data}".encode()
        ).hexdigest()
        assert params["sign"] == expected_sign
        assert "_m_h5_tk=test-token_123" in request.headers["cookie"]
        return httpx.Response(
            200,
            json={
                "ret": ["SUCCESS::调用成功"],
                "data": {
                    "item": {
                        "title": "MTop 淘宝商品",
                        "images": ["https://img.alicdn.com/product.jpg"],
                    },
                    "price": {"priceText": "17.52"},
                    "seller": {"shopName": "MTop 店铺"},
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        cookies=build_cookies(cookie_value, parser.cookie_domains),
    ) as client:
        metadata = await parser._fetch_api_metadata(client, "1067554939784")

    assert metadata == ProductMetadata(
        title="MTop 淘宝商品",
        price="¥17.52",
        shop="MTop 店铺",
        image_url="https://img.alicdn.com/product.jpg",
    )


async def test_taobao_rejects_cmd_escaped_cookie_before_mtop_request():
    cookie_value = (
        "_m_h5_tk=test-token_123; _m_h5_tk_enc=test-enc; cookie17=user^%^3D^%^3D"
    )
    parser = TaobaoParser({"cookies": {"taobao_cookies": cookie_value}})
    requested = False

    def handler(request):
        nonlocal requested
        requested = True
        return httpx.Response(200, json={"ret": ["SUCCESS::调用成功"], "data": {}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        cookies=build_cookies(cookie_value, parser.cookie_domains),
    ) as client:
        with pytest.raises(ValueError, match="Cookies 格式不正确"):
            await parser._fetch_api_metadata(client, "1067554939784")

    assert requested is False


async def test_taobao_maps_mtop_risk_response_to_cookie_error():
    cookie_value = "_m_h5_tk=test-token_123; _m_h5_tk_enc=test-enc"
    parser = TaobaoParser({"cookies": {"taobao_cookies": cookie_value}})

    def handler(request):
        return httpx.Response(
            200,
            json={
                "ret": ["RGV587_ERROR::SM::访问频繁"],
                "data": {"url": "https://h5api.m.taobao.com/private-token"},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        cookies=build_cookies(cookie_value, parser.cookie_domains),
    ) as client:
        with pytest.raises(CookieAccessError) as error:
            await parser._fetch_api_metadata(client, "1067554939784")

    assert "private-token" not in str(error.value)
    assert "test-token" not in str(error.value)


async def test_taobao_scopes_page_cookies_and_keeps_images_cookie_free(monkeypatch):
    parser = TaobaoParser({"cookies": {"taobao_cookies": "session=test-secret"}})
    page_cookie_domains = []
    image_cookies = []

    async def fetch_page(client, url, host_suffixes):
        page_cookie_domains.append(
            sorted(cookie.domain for cookie in client.cookies.jar)
        )
        return FetchedWebPage(
            "https://item.taobao.com/item.htm?id=123456",
            """
            <meta property="og:title" content="淘宝商品">
            <meta property="og:image" content="https://img.alicdn.com/main.jpg">
            <meta property="product:price:amount" content="88.00">
            """,
        )

    async def materialize(result, client, referer):
        image_cookies.extend(client.cookies.jar)
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(ParseContext(text="https://m.tb.cn/h.Abc123"))

    assert result.title == "淘宝商品"
    assert page_cookie_domains == [[".taobao.com", ".tb.cn", ".tmall.com"]]
    assert image_cookies == []


async def test_taobao_parse_rejects_untrusted_client_side_target(monkeypatch):
    parser = TaobaoParser({})
    requested = []

    async def fetch_page(client, url, host_suffixes):
        requested.append(url)
        return FetchedWebPage(
            "https://e.tb.cn/h.Abc123",
            "var url = 'https://item.taobao.com.evil.test/item.htm?id=123456';",
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(ParseContext(text="https://e.tb.cn/h.Abc123"))

    assert result.error == "淘宝/天猫分享链接未指向受支持的商品。"
    assert requested == ["https://e.tb.cn/h.Abc123"]


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
    assert result.extra_lines == []


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
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "淘宝/天猫内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"taobao_cookies": "session=test-secret"}},
            "淘宝/天猫内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_taobao_parse_reports_verification_page(
    monkeypatch, marker, config, expected
):
    parser = TaobaoParser(config)

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

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "淘宝/天猫内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"taobao_cookies": "session=test-secret"}},
            "淘宝/天猫内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_taobao_parse_maps_missing_metadata_to_cookie_error(
    monkeypatch, config, expected
):
    parser = TaobaoParser(config)

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.taobao.com/item.htm?id=123456",
            "<html></html>",
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(
        ParseContext(text="https://item.taobao.com/item.htm?id=123456")
    )

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "淘宝/天猫内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"taobao_cookies": "session=test-secret"}},
            "淘宝/天猫内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_taobao_maps_auth_status_to_cookie_error(
    monkeypatch, status_code, config, expected
):
    parser = TaobaoParser(config)

    async def fetch_page(client, url, host_suffixes):
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError("secret", request=request, response=response)

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.taobao.parser.fetch_trusted_html", fetch_page
    )

    result = await parser.parse(ParseContext(text="https://m.tb.cn/h.Abc123"))

    assert result.error == expected
    assert "test-secret" not in result.error


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
