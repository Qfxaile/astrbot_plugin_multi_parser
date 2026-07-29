import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.webpage import FetchedWebPage, TrustedWebPageError
from astrbot_multi_parser.platforms.pinduoduo import PinduoduoParser


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mobile.yangkeduo.com/goods.html?goods_id=123456", True),
        ("https://mobile.yangkeduo.com/goods.html?ps=Abc123", True),
        ("https://mobile.yangkeduo.com/goods2.html?ps=CQGwm6NMIa", True),
        ("https://p.pinduoduo.com/Abc123", True),
        ("https://mobile.yangkeduo.com/search_result.html?q=test", False),
        ("https://p.pinduoduo.com.evil.test/Abc123", False),
        ("http://mobile.yangkeduo.com/goods.html?goods_id=123456", False),
        ("https://mobile.yangkeduo.com:444/goods.html?goods_id=123456", False),
        ("https://mobile.yangkeduo.com/goods.html?goods_id=abc", False),
    ],
)
async def test_pinduoduo_match_accepts_only_supported_product_urls(url, expected):
    assert await PinduoduoParser({}).match(ParseContext(text=url)) is expected


def test_pinduoduo_builds_canonical_goods_and_ps_urls():
    parser = PinduoduoParser({})

    assert (
        parser._canonical_product_url(
            "https://mobile.yangkeduo.com/goods2.html?goods_id=795783843683"
        )
        == "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683"
    )
    assert (
        parser._canonical_product_url(
            "https://mobile.yangkeduo.com/goods.html?goods_id=123456&refer=secret"
        )
        == "https://mobile.yangkeduo.com/goods.html?goods_id=123456"
    )
    assert (
        parser._canonical_product_url(
            "https://mobile.yangkeduo.com/goods.html?ps=Abc123&refer=secret"
        )
        == "https://mobile.yangkeduo.com/goods.html?ps=Abc123"
    )
    assert (
        parser._canonical_product_url(
            "https://mobile.yangkeduo.com/goods.html?ps=Abc123",
            goods_id="789012",
        )
        == "https://mobile.yangkeduo.com/goods.html?goods_id=789012"
    )
    assert (
        parser._canonical_product_url(
            "https://mobile.yangkeduo.com/goods.html?goods_id=123456",
            goods_id="789012",
        )
        == "https://mobile.yangkeduo.com/goods.html?goods_id=123456"
    )


async def test_pinduoduo_parse_converts_integer_cent_price(monkeypatch):
    parser = PinduoduoParser({})
    html = """
    <script type="application/json">
      {"goods":{"goodsName":"拼多多测试商品","minGroupPrice":129900,
                "mallName":"拼多多测试店",
                "hdThumbUrl":"https://img.pddpic.com/main.jpg",
                "goodsId":"123456"}}
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?ps=Abc123",
            html,
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(ParseContext(text="https://p.pinduoduo.com/Abc123"))

    assert result.title == "拼多多测试商品"
    assert result.cover_urls == ["https://img.pddpic.com/main.jpg"]
    assert result.extra_lines == [
        "价格: ¥1299.00",
        "店铺: 拼多多测试店",
        "商品链接: https://mobile.yangkeduo.com/goods.html?goods_id=123456",
    ]


async def test_pinduoduo_scopes_page_cookies_and_keeps_images_cookie_free(
    monkeypatch,
):
    parser = PinduoduoParser({"cookies": {"pinduoduo_cookies": "session=test-secret"}})
    page_cookie_domains = []
    image_cookies = []

    async def fetch_page(client, url, host_suffixes):
        page_cookie_domains.append(
            sorted(cookie.domain for cookie in client.cookies.jar)
        )
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods2.html?goods_id=795783843683",
            """
            <meta property="og:title" content="拼多多商品">
            <meta property="og:image" content="https://img.pddpic.com/main.jpg">
            """,
        )

    async def materialize(result, client, referer):
        image_cookies.extend(client.cookies.jar)
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods2.html?ps=Abc123")
    )

    assert result.title == "拼多多商品"
    assert page_cookie_domains == [[".pinduoduo.com", ".yangkeduo.com"]]
    assert image_cookies == []


async def test_pinduoduo_string_price_is_not_divided_again(monkeypatch):
    parser = PinduoduoParser({})
    html = """
    <script type="application/json">
      {"goods":{"goodsName":"字符串价格商品","minGroupPrice":"12.90"}}
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?goods_id=123456",
            html,
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods.html?goods_id=123456")
    )

    assert result.extra_lines[0] == "价格: ¥12.90"


async def test_pinduoduo_uses_json_ld_before_platform_and_og(monkeypatch):
    parser = PinduoduoParser({})
    html = """
    <script type="application/ld+json">
      {"@type":"Product","name":"JSON-LD商品",
       "offers":{"price":"29.00"}}
    </script>
    <script type="application/json">
      {"goods":{"goodsName":"内嵌商品","mallName":"测试店",
                "goodsId":"123456"}}
    </script>
    <meta property="og:title" content="OG商品">
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?ps=Abc123",
            html,
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(ParseContext(text="https://p.pinduoduo.com/Abc123"))

    assert result.title == "JSON-LD商品"
    assert result.extra_lines == [
        "价格: ¥29.00",
        "店铺: 测试店",
        "商品链接: https://mobile.yangkeduo.com/goods.html?goods_id=123456",
    ]


async def test_pinduoduo_keeps_clean_ps_link_when_goods_id_is_missing(
    monkeypatch,
):
    parser = PinduoduoParser({})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?ps=Abc123&refer=secret",
            '<meta property="og:title" content="公开商品">',
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(ParseContext(text="https://p.pinduoduo.com/Abc123"))

    assert result.extra_lines == [
        "商品链接: https://mobile.yangkeduo.com/goods.html?ps=Abc123"
    ]


async def test_pinduoduo_short_link_must_resolve_to_goods_page(monkeypatch):
    parser = PinduoduoParser({})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/search_result.html?q=test",
            "<html></html>",
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(ParseContext(text="https://p.pinduoduo.com/Abc123"))

    assert result.error == "拼多多分享链接未指向受支持的商品。"


@pytest.mark.parametrize("marker", ["验证码", "安全验证", "登录后查看"])
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "拼多多内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"pinduoduo_cookies": "session=test-secret"}},
            "拼多多内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_pinduoduo_reports_verification_page(
    monkeypatch, marker, config, expected
):
    parser = PinduoduoParser(config)

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?goods_id=123456",
            f"<html>{marker}</html>",
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods.html?goods_id=123456")
    )

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "拼多多内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"pinduoduo_cookies": "session=test-secret"}},
            "拼多多内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_pinduoduo_reports_need_login_page(monkeypatch, config, expected):
    parser = PinduoduoParser(config)

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods2.html?goods_id=795783843683",
            """
            <meta property="og:title" content="拼多多商城">
            <script>window.rawData={"store":{"initDataObj":{"needLogin":true}}}</script>
            """,
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods2.html?ps=CQGwm6NMIa")
    )

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "拼多多内容获取失败，可能需要配置 Cookies，请在插件配置中填写后重试。"),
        (
            {"cookies": {"pinduoduo_cookies": "session=test-secret"}},
            "拼多多内容获取失败，配置的 Cookies 可能已失效，请更新后重试。",
        ),
    ],
)
async def test_pinduoduo_maps_auth_status_to_cookie_error(
    monkeypatch, status_code, config, expected
):
    parser = PinduoduoParser(config)

    async def fetch_page(client, url, host_suffixes):
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError("secret", request=request, response=response)

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods2.html?ps=Abc123")
    )

    assert result.error == expected
    assert "test-secret" not in result.error


@pytest.mark.parametrize("status_code", [404, 410])
async def test_pinduoduo_reports_unavailable_product(monkeypatch, status_code):
    parser = PinduoduoParser({})

    async def fetch_page(client, url, host_suffixes):
        request = httpx.Request("GET", url)
        response = httpx.Response(status_code, request=request)
        raise httpx.HTTPStatusError("secret", request=request, response=response)

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods.html?goods_id=123456")
    )

    assert result.error == "该拼多多商品已下架或不存在。"


async def test_pinduoduo_filters_untrusted_image(monkeypatch):
    parser = PinduoduoParser({})
    html = """
    <script type="application/json">
      {"goods":{"goodsName":"拼多多商品",
                "hdThumbUrl":"https://pddpic.com.evil.test/main.jpg"}}
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?goods_id=123456",
            html,
        )

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods.html?goods_id=123456")
    )

    assert result.title == "拼多多商品"
    assert result.cover_urls == []


async def test_pinduoduo_maps_safe_and_network_errors(monkeypatch):
    parser = PinduoduoParser({})

    async def unsafe_page(client, url, host_suffixes):
        raise TrustedWebPageError("商品分享链接跳转到不可信域名。")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        unsafe_page,
    )
    safe_error = await parser.parse(ParseContext(text="https://p.pinduoduo.com/Abc123"))
    assert safe_error.error == "商品分享链接跳转到不可信域名。"

    async def failed_page(client, url, host_suffixes):
        raise httpx.ConnectError("private-network-detail")

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        failed_page,
    )
    network_error = await parser.parse(
        ParseContext(text="https://p.pinduoduo.com/Abc123")
    )
    assert network_error.error == "拼多多商品请求失败，请稍后重试。"
    assert "private-network-detail" not in network_error.error
