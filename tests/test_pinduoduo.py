import json

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.product_metadata import ProductMetadata
from astrbot_multi_parser.core.webpage import FetchedWebPage, TrustedWebPageError
from astrbot_multi_parser.platforms.pinduoduo import PinduoduoParser


def test_pinduoduo_result_only_displays_product_content():
    result = PinduoduoParser({})._build_result(
        ProductMetadata(title="拼多多商品", price="¥66.00", shop="拼多多店铺"),
        "https://mobile.yangkeduo.com/goods.html?goods_id=123456",
    )

    assert result.extra_lines == []


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


async def test_pinduoduo_parse_hides_price(monkeypatch):
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
    assert result.extra_lines == []


async def test_pinduoduo_parse_reads_goods_from_window_raw_data(monkeypatch):
    parser = PinduoduoParser({"cookies": {"pinduoduo_cookies": "session=test-secret"}})
    html = """
    <meta property="og:title" content="拼多多商城">
    <meta property="og:image"
          content="https://funimg.pddpic.com/base/share_logo.jpg">
    <script>
      window.rawData = {"store":{"initDataObj":{"goods":{
        "goodsName":"真实拼多多商品","minGroupPrice":2590,
        "mallName":"真实店铺",
        "hdThumbUrl":"https://img.pddpic.com/product.jpg",
        "goodsId":"795783843683"}}}};
    </script>
    """

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods2.html?goods_id=795783843683",
            html,
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods2.html?ps=CQGwm6NMIa")
    )

    assert result.title == "真实拼多多商品"
    assert result.cover_urls == ["https://img.pddpic.com/product.jpg"]
    assert result.extra_lines == []


async def test_pinduoduo_rejects_generic_store_metadata(monkeypatch):
    parser = PinduoduoParser({"cookies": {"pinduoduo_cookies": "session=test-secret"}})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods2.html?goods_id=795783843683",
            """
            <meta property="og:title" content="拼多多商城">
            <meta property="og:image"
                  content="https://funimg.pddpic.com/base/share_logo.jpg">
            """,
        )

    async def fetch_oak(client, goods_id):
        raise parser.cookie_access_error()

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "_fetch_oak_metadata", fetch_oak)

    result = await parser.parse(
        ParseContext(text="https://mobile.yangkeduo.com/goods2.html?ps=CQGwm6NMIa")
    )

    assert result.title == ""
    assert result.cover_urls == []
    assert (
        result.error == "拼多多内容获取失败，配置的 Cookies 可能已失效，请更新后重试。"
    )


async def test_pinduoduo_fetches_oak_product_with_configured_cookies():
    parser = PinduoduoParser(
        {
            "cookies": {
                "pinduoduo_cookies": ("session=test-secret; pdd_user_id=1234567890123")
            }
        }
    )
    captured_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(
            200,
            json={
                "goods": {
                    "goods_id": 795783843683,
                    "goods_name": "Oak 真实商品",
                    "min_group_price": 2590,
                    "hd_thumb_url": "https://img.pddpic.com/oak-product.jpg",
                },
                "mall_entrance": {"mall_data": {"mall_name": "Oak 真实店铺"}},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        cookies=httpx.Cookies(
            {"session": "test-secret", "pdd_user_id": "1234567890123"}
        ),
    ) as client:
        metadata, goods_id = await parser._fetch_oak_metadata(
            client,
            "795783843683",
        )

    assert captured_request is not None
    assert captured_request.method == "POST"
    assert str(captured_request.url) == (
        "https://mobile.yangkeduo.com/proxy/api/api/oak/integration/render"
    )
    assert "pdduid" not in captured_request.url.params
    assert captured_request.headers["referer"] == (
        "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683"
    )
    assert "session=test-secret" in captured_request.headers["cookie"]
    assert "pdd_user_id=1234567890123" in captured_request.headers["cookie"]
    assert json.loads(captured_request.content) | {"client_time": 0, "page_id": ""} == {
        "page_version": 7,
        "goods_id": "795783843683",
        "page_from": 0,
        "hostname": "mobile.yangkeduo.com",
        "client_time": 0,
        "page_sn": 10014,
        "page_id": "",
        "front_supports": [
            "community_purchase",
            "split_info_section",
            "render_opt_2022",
            "new_price_bottom",
            "custom_sku",
        ],
    }
    assert metadata.title == "Oak 真实商品"
    assert metadata.price == "¥25.90"
    assert metadata.shop == "Oak 真实店铺"
    assert metadata.image_url == "https://img.pddpic.com/oak-product.jpg"
    assert goods_id == "795783843683"


@pytest.mark.parametrize(
    ("goods_images", "expected"),
    [
        ({"thumb_url": "https://img.pddpic.com/thumb.jpg"}, "thumb.jpg"),
        (
            {"gallery": [{"url": "https://img.pddpic.com/gallery.jpg"}]},
            "gallery.jpg",
        ),
    ],
)
def test_pinduoduo_oak_metadata_uses_available_product_image(
    goods_images,
    expected,
):
    metadata = PinduoduoParser._extract_oak_metadata(
        {
            "goods": {
                "goods_id": "795783843683",
                "goods_name": "多字段主图商品",
                **goods_images,
            }
        },
        "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
    )

    assert metadata.image_url == f"https://img.pddpic.com/{expected}"


def test_pinduoduo_oak_metadata_prefers_gallery_over_app_icon():
    metadata = PinduoduoParser._extract_oak_metadata(
        {
            "goods": {
                "goods_id": "795783843683",
                "goods_name": "图库商品",
                "image_url": "https://funimg.pddpic.com/base/share_logo.jpg",
                "gallery": [{"url": "https://img.pddpic.com/product.jpg"}],
            }
        },
        "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
    )

    assert metadata.image_url == "https://img.pddpic.com/product.jpg"


def test_pinduoduo_oak_metadata_rejects_app_icon_without_product_image():
    metadata = PinduoduoParser._extract_oak_metadata(
        {
            "goods": {
                "goods_id": "795783843683",
                "goods_name": "无主图商品",
                "image_url": "https://funimg.pddpic.com/base/share_logo.jpg",
            }
        },
        "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
    )

    assert metadata.image_url == ""


def test_pinduoduo_oak_metadata_reads_root_price_section():
    metadata = PinduoduoParser._extract_oak_metadata(
        {
            "goods": {
                "goods_id": "795783843683",
                "goods_name": "Oak 价格商品",
                "gallery": [{"url": "https://img.pddpic.com/product.jpg"}],
            },
            "price": {"min_group_price": 2590},
        },
        "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
    )

    assert metadata.price == "¥25.90"


async def test_pinduoduo_uses_oak_gallery_when_page_image_is_app_icon(
    monkeypatch,
):
    parser = PinduoduoParser({"cookies": {"pinduoduo_cookies": "session=test-secret"}})
    oak_requests = []

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
            """
            <meta property="og:title" content="正确商品标题">
            <meta property="product:price:amount" content="9.90">
            <meta property="og:image"
                  content="https://funimg.pddpic.com/base/share_logo.jpg">
            """,
        )

    async def fetch_oak(client, goods_id):
        oak_requests.append(goods_id)
        payload = {
            "goods": {
                "goods_id": goods_id,
                "goods_name": "正确商品标题",
                "gallery": [{"url": "https://img.pddpic.com/real-product.jpeg"}],
            }
        }
        return (
            parser._extract_oak_metadata(
                payload,
                "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
            ),
            goods_id,
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "_fetch_oak_metadata", fetch_oak)
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(
        ParseContext(
            text=("https://mobile.yangkeduo.com/goods.html?goods_id=795783843683")
        )
    )

    assert oak_requests == ["795783843683"]
    assert result.title == "正确商品标题"
    assert result.cover_urls == ["https://img.pddpic.com/real-product.jpeg"]


def test_pinduoduo_page_metadata_uses_camel_case_thumb_url():
    metadata, goods_id = PinduoduoParser._extract_platform_metadata(
        """
        <script>
          window.rawData={"store":{"initDataObj":{"goods":{
            "goodsId":"795783843683",
            "goodsName":"页面主图商品",
            "thumbUrl":"https://img.pddpic.com/page-thumb.jpg"}}}};
        </script>
        """,
        "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
    )

    assert goods_id == "795783843683"
    assert metadata.image_url == "https://img.pddpic.com/page-thumb.jpg"


async def test_pinduoduo_uses_oak_fallback_when_page_has_no_product(monkeypatch):
    parser = PinduoduoParser({"cookies": {"pinduoduo_cookies": "session=test-secret"}})

    async def fetch_page(client, url, host_suffixes):
        return FetchedWebPage(
            "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
            '<meta property="og:title" content="拼多多商城">',
        )

    async def fetch_oak(client, goods_id):
        assert goods_id == "795783843683"
        return (
            parser._extract_oak_metadata(
                {
                    "goods": {
                        "goods_id": goods_id,
                        "goods_name": "接口商品",
                        "min_group_price": 1990,
                        "hd_thumb_url": "https://img.pddpic.com/api.jpg",
                    },
                    "mall_entrance": {"mall_data": {"mall_name": "接口店铺"}},
                },
                "https://mobile.yangkeduo.com/goods.html?goods_id=795783843683",
            ),
            goods_id,
        )

    async def materialize(result, client, referer):
        return result

    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.pinduoduo.parser.fetch_trusted_html",
        fetch_page,
    )
    monkeypatch.setattr(parser, "_fetch_oak_metadata", fetch_oak)
    monkeypatch.setattr(parser, "materialize_images", materialize)

    result = await parser.parse(
        ParseContext(
            text=("https://mobile.yangkeduo.com/goods.html?goods_id=795783843683")
        )
    )

    assert result.title == "接口商品"
    assert result.cover_urls == ["https://img.pddpic.com/api.jpg"]
    assert result.extra_lines == []


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (403, {"error_code": 40001, "error_msg": "secret-response"}),
        (200, {"encrypt_status": 1, "error_msg": "secret-response"}),
    ],
)
async def test_pinduoduo_oak_auth_failure_does_not_leak_details(
    status_code,
    payload,
):
    parser = PinduoduoParser({"cookies": {"pinduoduo_cookies": "session=test-secret"}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError) as exc_info:
            await parser._fetch_oak_metadata(client, "795783843683")

    message = str(exc_info.value)
    assert message == "拼多多内容获取失败，配置的 Cookies 可能已失效，请更新后重试。"
    assert "test-secret" not in message
    assert "secret-response" not in message


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
            <meta property="product:price:amount" content="66.00">
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


async def test_pinduoduo_hides_string_price(monkeypatch):
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

    assert result.extra_lines == []


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
    assert result.extra_lines == []


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

    assert result.extra_lines == []


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
