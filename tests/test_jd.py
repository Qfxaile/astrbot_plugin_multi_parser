import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.webpage import FetchedWebPage, TrustedWebPageError
from astrbot_multi_parser.platforms.jd import JDParser


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://item.jd.com/100012043978.html", True),
        ("https://item.m.jd.com/product/100012043978.html", True),
        ("https://3.cn/abc-def", True),
        ("https://u.jd.com/abc123", True),
        ("https://search.jd.com/Search?keyword=test", False),
        ("https://item.jd.com.evil.test/100012043978.html", False),
        ("http://item.jd.com/100012043978.html", False),
        ("https://item.jd.com:444/100012043978.html", False),
    ],
)
async def test_jd_match_accepts_only_supported_product_urls(url, expected):
    assert await JDParser({}).match(ParseContext(text=url)) is expected


def test_jd_builds_clean_canonical_urls():
    parser = JDParser({})

    assert (
        parser._canonical_product_url(
            "https://item.jd.com/100012043978.html?utm_source=secret"
        )
        == "https://item.jd.com/100012043978.html"
    )
    assert (
        parser._canonical_product_url(
            "https://item.m.jd.com/product/100012043978.html?utm_source=secret"
        )
        == "https://item.jd.com/100012043978.html"
    )


async def test_jd_parse_uses_json_ld_then_platform_data(monkeypatch):
    parser = JDParser({})
    materialized = []
    html = """
    <script type="application/ld+json">
      {"@type":"Product","name":"JSON-LD京东商品",
       "offers":{"price":"99.00","priceCurrency":"CNY"}}
    </script>
    <script type="application/json">
      {"product":{"skuName":"内嵌京东商品","price":"88.00",
                  "shopName":"京东测试店",
                  "image":"https://img10.360buyimg.com/n1/main.jpg"}}
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.jd.com/100012043978.html?utm_source=secret",
            html,
        )

    async def materialize(result, client, referer):
        materialized.append(referer)
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(ParseContext(text="https://3.cn/abc-def"))

    assert result.title == "JSON-LD京东商品"
    assert result.cover_urls == ["https://img10.360buyimg.com/n1/main.jpg"]
    assert result.extra_lines == [
        "价格: ¥99.00",
        "店铺: 京东测试店",
        "商品链接: https://item.jd.com/100012043978.html",
    ]
    assert materialized == ["https://item.jd.com/100012043978.html?utm_source=secret"]


async def test_jd_parse_reads_item_info_embedded_objects(monkeypatch):
    parser = JDParser({})
    html = """
    <meta charset="utf-8">
    <script>
    window._itemInfo = ({
      "product":{"skuName":"西部数据固态硬盘",
                 "imageurl":"jfs/t1/main.png"},
      "price":"5??9",
      "stock":{"D":{"shopName":"西部数据官方旗舰店"}},
      invalidJavaScriptKey: true
    });
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.m.jd.com/product/10060144289722.html",
            html,
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(ParseContext(text="https://3.cn/2Xhi-9CP"))

    assert result.title == "西部数据固态硬盘"
    assert result.cover_urls == ["https://img10.360buyimg.com/n1/jfs/t1/main.png"]
    assert result.extra_lines == [
        "店铺: 西部数据官方旗舰店",
        "商品链接: https://item.jd.com/10060144289722.html",
    ]


async def test_jd_scopes_page_cookies_and_keeps_images_cookie_free(monkeypatch):
    parser = JDParser({"cookies": {"jd_cookies": "session=test-secret"}})
    page_cookie_domains = []
    image_cookies = []

    async def fetch_page(client, url, host_suffixes):
        page_cookie_domains.append(
            sorted(cookie.domain for cookie in client.cookies.jar)
        )
        return FetchedWebPage(
            "https://item.jd.com/100012043978.html",
            """
            <meta property="og:title" content="京东商品">
            <meta property="og:image"
                  content="https://img10.360buyimg.com/n1/main.jpg">
            """,
        )

    async def materialize(result, client, referer):
        image_cookies.extend(client.cookies.jar)
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(ParseContext(text="https://3.cn/abc-def"))

    assert result.title == "京东商品"
    assert page_cookie_domains == [[".3.cn", ".jd.com"]]
    assert image_cookies == []


async def test_jd_parse_falls_back_to_open_graph_without_optional_fields(
    monkeypatch,
):
    parser = JDParser({})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.jd.com/100012043978.html",
            '<meta property="og:title" content="京东公开商品">',
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://item.jd.com/100012043978.html")
    )

    assert result.title == "京东公开商品"
    assert result.cover_urls == []
    assert result.extra_lines == ["商品链接: https://item.jd.com/100012043978.html"]


async def test_jd_parse_rejects_short_link_to_non_product_page(monkeypatch):
    parser = JDParser({})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage("https://www.jd.com/", "<html></html>")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(ParseContext(text="https://u.jd.com/abc123"))

    assert result.error == "京东分享链接未指向受支持的商品。"


@pytest.mark.parametrize("marker", ["验证码", "安全验证", "登录后查看"])
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "京东内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"jd_cookies": "session=test-secret"}},
            "京东内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_jd_parse_reports_verification_page(
    monkeypatch, marker, config, expected
):
    parser = JDParser(config)

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.jd.com/100012043978.html",
            f"<html>{marker}</html>",
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://item.jd.com/100012043978.html")
    )

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "京东内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"jd_cookies": "session=test-secret"}},
            "京东内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_jd_maps_auth_status_to_cookie_error(
    monkeypatch, status_code, config, expected
):
    parser = JDParser(config)

    async def fetch_page(client, url, host_suffixes):
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError("secret", request=request, response=response)

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(ParseContext(text="https://3.cn/abc-def"))

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize("status_code", [404, 410])
async def test_jd_parse_reports_unavailable_product(monkeypatch, status_code):
    parser = JDParser({})

    async def fetch_page(client, url, host_suffixes):
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError("secret", request=request, response=response)

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://item.jd.com/100012043978.html")
    )

    assert result.error == "该京东商品已下架或不存在。"


async def test_jd_parse_filters_untrusted_image(monkeypatch):
    parser = JDParser({})
    html = """
    <script type="application/json">
      {"product":{"skuName":"京东商品",
                  "image":"https://360buyimg.com.evil.test/main.jpg"}}
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://item.jd.com/100012043978.html",
            html,
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://item.jd.com/100012043978.html")
    )

    assert result.title == "京东商品"
    assert result.cover_urls == []


async def test_jd_parse_maps_safe_and_network_errors(monkeypatch):
    parser = JDParser({})

    async def unsafe_page(client, url, host_suffixes):
        raise TrustedWebPageError("商品分享链接跳转到不可信域名。")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        unsafe_page,
    )
    safe_error = await parser.parse(ParseContext(text="https://3.cn/abc-def"))
    assert safe_error.error == "商品分享链接跳转到不可信域名。"

    async def failed_page(client, url, host_suffixes):
        raise httpx.ConnectError("private-network-detail")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.jd.parser.fetch_trusted_html",
        failed_page,
    )
    network_error = await parser.parse(ParseContext(text="https://3.cn/abc-def"))
    assert network_error.error == "京东商品请求失败，请稍后重试。"
    assert "private-network-detail" not in network_error.error
