import json

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.platforms.bilibili import mall
from astrbot_multi_parser.platforms.bilibili import parser as bilibili


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "kind", "item_id"),
    [
        (
            "https://mall.bilibili.com/neul-next/ticket-renovation/"
            "detail.html?id=1004079",
            "ticket_new",
            "1004079",
        ),
        (
            "https://show.bilibili.com/platform/detail.html?msource=share&id=1004079",
            "ticket_old",
            "1004079",
        ),
        (
            "https://mall.bilibili.com/neul-next/detailuniversal/detail.html?"
            "isMerchant=1&itemsId=13365078",
            "product",
            "13365078",
        ),
        (
            "https://mall.bilibili.com/detail.html?itemsId=13365078",
            "product",
            "13365078",
        ),
        (
            "https://mall.bilibili.com/neul-next/index.html?"
            "page=detailuniversal_detail&itemsId=13365078",
            "product",
            "13365078",
        ),
        (
            "https://mall.bilibili.com/neul-next/index.html?"
            "page=mall-up_itemDetail&itemsId=1109133081",
            "workshop",
            "1109133081",
        ),
        (
            "https://gf.bilibili.com/item/detail/1109133081",
            "workshop",
            "1109133081",
        ),
        (
            "https://mall.bilibili.com/neul-next/index.html?"
            "page=magic-market_detail&itemsId=198339499233",
            "market",
            "198339499233",
        ),
    ],
)
async def test_matches_supported_bilibili_mall_detail_urls(url, kind, item_id):
    target = mall.find_mall_target(f"分享链接：{url}。")

    assert target is not None
    assert (target.kind, target.item_id, target.url) == (kind, item_id, url)
    assert await bilibili.BilibiliParser({}).match(ParseContext(text=url)) is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://mall.bilibili.com/neul-next/index.html?page=magic-market_index",
        "https://mall.bilibili.com/neul-next/index.html?page=order&itemsId=123",
        "https://mall.bilibili.com/neul-next/ticket-renovation/detail.html?id=abc",
        "https://mall.bilibili.com/neul-next/detailuniversal/detail.html?itemsId=1&itemsId=2",
        "http://mall.bilibili.com/detail.html?itemsId=123",
        "https://mall.bilibili.com.example/detail.html?itemsId=123",
    ],
)
async def test_rejects_non_detail_or_unsafe_bilibili_mall_urls(url):
    assert mall.find_mall_target(url) is None
    assert await bilibili.BilibiliParser({}).match(ParseContext(text=url)) is False


def test_parses_new_ticket_payload_with_summary_and_detail_images():
    payload = {
        "code": 0,
        "success": True,
        "data": {
            "projectId": 1004079,
            "projectName": "沈阳·第二十一届 SSCA 动漫游戏博览会",
            "projectLabel": "2026.10.01-10.04（以现场为准）",
            "salablePriceLow": 7000,
            "salablePriceHigh": 11800,
            "skuVenueInfo": {
                "name": "工业展览馆",
                "province_name": "辽宁",
                "city_name": "沈阳",
                "address_detail": "和平区青年大街 1 号",
            },
            "basicInfoFloorVO": {
                "brief": "大型动漫游戏博览会",
                "imageList": ["//i1.hdslb.com/banner.jpg"],
            },
            "detailDescFloorVO": {
                "performanceDesc": {
                    "list": [
                        {
                            "module": "activity_content",
                            "details": (
                                '<p><img src="//i1.hdslb.com/detail-1.jpg">'
                                '<img src="//i2.hdslb.com/detail-2.jpg"></p>'
                            ),
                        }
                    ]
                }
            },
        },
    }

    result = mall.mall_detail_result(mall.parse_ticket_detail(payload))

    assert result.title == "沈阳·第二十一届 SSCA 动漫游戏博览会"
    assert result.description == "大型动漫游戏博览会"
    assert result.extra_lines == [
        "类型: 会员购票务",
        "票价: ¥70.00 - ¥118.00",
        "活动日期: 2026.10.01-10.04（以现场为准）",
        "场馆: 工业展览馆",
        "地址: 辽宁 沈阳 和平区青年大街 1 号",
    ]
    assert result.cover_urls == ["https://i1.hdslb.com/banner.jpg"]
    assert result.image_urls == [
        "https://i1.hdslb.com/detail-1.jpg",
        "https://i2.hdslb.com/detail-2.jpg",
    ]


def test_parses_legacy_ticket_payload():
    payload = {
        "code": 0,
        "data": {
            "name": "旧版票务活动",
            "description": "活动摘要",
            "project_label": "2026.08.01",
            "price_low": 5000,
            "price_high": 5000,
            "banner": "//i0.hdslb.com/legacy-ticket.jpg",
            "venue_info": {"name": "测试场馆", "address_detail": "测试地址"},
            "merchant": {"company": "测试主办方"},
        },
    }

    result = mall.mall_detail_result(mall.parse_ticket_detail(payload))

    assert result.title == "旧版票务活动"
    assert result.extra_lines == [
        "类型: 会员购票务",
        "票价: ¥50.00",
        "活动日期: 2026.08.01",
        "主办方: 测试主办方",
        "场馆: 测试场馆",
        "地址: 测试地址",
    ]


def test_parses_universal_product_payload():
    payload = {
        "code": 0,
        "success": True,
        "data": {
            "name": "会员购普通商品",
            "price": "1.3",
            "shopInfoVO": {"shopName": "测试小店"},
            "img": ["//i0.hdslb.com/main.png"],
            "mobileDesc": (
                '<section>商品简短说明</section><img src="//i0.hdslb.com/detail.png">'
            ),
            "itemsDeliveryInfo": {"deliveryMainDesc": "线上交付，支付后自动发货"},
            "attrList": [
                {"attrName": "尺寸", "attrValue": ["高 32 格"]},
                {"attrName": "使用范围", "attrValue": ["个人使用"]},
            ],
        },
    }

    result = mall.mall_detail_result(mall.parse_product_detail(payload))

    assert result.title == "会员购普通商品"
    assert result.description == "商品简短说明"
    assert result.extra_lines == [
        "类型: 会员购商品",
        "价格: ¥1.3",
        "店铺: 测试小店",
        "交付: 线上交付，支付后自动发货",
        "尺寸: 高 32 格",
        "使用范围: 个人使用",
    ]
    assert result.cover_urls == ["https://i0.hdslb.com/main.png"]
    assert result.image_urls == ["https://i0.hdslb.com/detail.png"]


def test_parses_workshop_product_payload():
    payload = {
        "code": 0,
        "success": True,
        "data": {
            "name": "工房商品",
            "price": 100,
            "description": "工房商品说明",
            "mainImgList": ["//i0.hdslb.com/workshop.jpg"],
            "detailImgList": [{"url": "//i0.hdslb.com/workshop-detail.jpg"}],
            "shopInfo": {"shopUserNickName": "UP 主小店"},
            "itemsDiscountPriceVO": {"discountPrice": 10},
            "itemsTag": {"deliveryTagList": ["线上交付", "支付后自动发货"]},
        },
    }

    result = mall.mall_detail_result(mall.parse_workshop_detail(payload))

    assert result.title == "工房商品"
    assert result.extra_lines == [
        "类型: 会员购工房",
        "价格: ¥1.00",
        "粉丝价: ¥0.10",
        "店铺: UP 主小店",
        "交付: 线上交付 / 支付后自动发货",
    ]
    assert result.image_urls == ["https://i0.hdslb.com/workshop-detail.jpg"]


def test_parses_market_product_payload_with_fukubukuro_warning():
    payload = {
        "code": 0,
        "success": True,
        "data": {
            "c2cItemsName": "福袋商品",
            "showPrice": "20",
            "saleStatus": 2,
            "uname": "开***",
            "detailDtoList": [{"name": "福袋商品", "img": "//i0.hdslb.com/market.png"}],
        },
    }

    result = mall.mall_detail_result(mall.parse_market_detail(payload))

    assert result.title == "福袋商品"
    assert result.extra_lines == [
        "类型: 会员购市集",
        "价格: ¥20",
        "状态: 已成交",
        "卖家: 开***",
        "提示: 福袋商品内容具有不确定性",
    ]


def test_mall_result_keeps_six_unique_trusted_images():
    detail = mall.MallDetail(
        title="图片上限",
        images=tuple(
            [f"//i0.hdslb.com/{index}.jpg" for index in range(8)]
            + ["//i0.hdslb.com/0.jpg", "https://example.com/external.jpg"]
        ),
    )

    result = mall.mall_detail_result(detail)

    assert [*result.cover_urls, *result.image_urls] == [
        f"https://i0.hdslb.com/{index}.jpg" for index in range(6)
    ]


@pytest.mark.asyncio
async def test_new_ticket_request_uses_json_headers_cookie_and_public_images(
    monkeypatch,
    assert_temporary_image,
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/mall-search-items/items_detail/info":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "success": True,
                    "data": {
                        "projectName": "新版票务活动",
                        "basicInfoFloorVO": {
                            "imageList": ["//i0.hdslb.com/ticket.jpg"]
                        },
                    },
                },
                request=request,
            )
        return httpx.Response(200, content=b"ticket-image", request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser(
        {"bilibili_cookies": "SESSDATA=mall-session"}
    ).parse(
        ParseContext(
            text=(
                "https://mall.bilibili.com/neul-next/ticket-renovation/"
                "detail.html?id=1004079"
            )
        )
    )

    api_request, image_request = requests
    assert api_request.method == "POST"
    assert json.loads(api_request.content) == {
        "itemsId": 1004079,
        "itemsDetailPageType": 3,
    }
    assert api_request.headers["Origin"] == "https://mall.bilibili.com"
    assert "id=1004079" in api_request.headers["Referer"]
    assert "SESSDATA=mall-session" in api_request.headers["Cookie"]
    assert "Cookie" not in image_request.headers
    assert image_request.headers["Referer"].endswith("id=1004079")
    assert_temporary_image(result, result.cover_urls[0], b"ticket-image")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "api_path", "param_name", "payload", "title"),
    [
        (
            "https://mall.bilibili.com/neul-next/detailuniversal/detail.html?"
            "itemsId=13365078",
            "/mall-c-search/items/info",
            "itemsId",
            {"code": 0, "data": {"name": "普通商品", "img": []}},
            "普通商品",
        ),
        (
            "https://gf.bilibili.com/item/detail/1109133081",
            "/mall-up-search/items/info",
            "itemsId",
            {"code": 0, "data": {"name": "工房商品", "mainImgList": []}},
            "工房商品",
        ),
        (
            "https://mall.bilibili.com/neul-next/index.html?"
            "page=magic-market_detail&itemsId=198339499233",
            "/mall-magic-c/internet/c2c/items/queryC2cItemsDetail",
            "c2cItemsId",
            {"code": 0, "data": {"c2cItemsName": "市集商品"}},
            "市集商品",
        ),
    ],
)
async def test_dispatches_product_detail_to_its_single_api(
    monkeypatch,
    url,
    api_path,
    param_name,
    payload,
    title,
):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=payload, request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(ParseContext(text=url))

    assert result.title == title
    assert len(requests) == 1
    assert requests[0].url.path == api_path
    assert requests[0].url.params[param_name] in url


@pytest.mark.asyncio
async def test_new_ticket_falls_back_to_legacy_api(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/mall-search-items/items_detail/info":
            return httpx.Response(
                200,
                json={"code": 11119999, "success": False, "message": "系统异常"},
                request=request,
            )
        return httpx.Response(
            200,
            json={"code": 0, "data": {"name": "旧接口回退活动"}},
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(
        ParseContext(
            text=(
                "https://mall.bilibili.com/neul-next/ticket-renovation/"
                "detail.html?id=1004079"
            )
        )
    )

    assert result.title == "旧接口回退活动"
    assert [request.url.path for request in requests] == [
        "/mall-search-items/items_detail/info",
        "/api/ticket/project/getV2",
    ]


@pytest.mark.asyncio
async def test_mall_business_auth_error_uses_cookie_hint(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": -101, "message": "账号未登录"},
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    with pytest.raises(CookieAccessError, match="Cookies 可能已失效"):
        await bilibili.BilibiliParser({"bilibili_cookies": "SESSDATA=secret"}).parse(
            ParseContext(
                text=(
                    "https://mall.bilibili.com/neul-next/detailuniversal/"
                    "detail.html?itemsId=13365078"
                )
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (404, "B站会员购内容已下架或不存在。"),
        (500, "B站会员购请求失败，请稍后重试。"),
    ],
)
async def test_mall_http_errors_return_readable_result(
    monkeypatch,
    status_code,
    expected_error,
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(
        ParseContext(
            text=(
                "https://mall.bilibili.com/neul-next/detailuniversal/"
                "detail.html?itemsId=13365078"
            )
        )
    )

    assert result.error == expected_error


@pytest.mark.asyncio
async def test_mall_invalid_json_returns_readable_result(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(
        ParseContext(
            text=(
                "https://mall.bilibili.com/neul-next/detailuniversal/"
                "detail.html?itemsId=13365078"
            )
        )
    )

    assert result.error == "B站会员购响应异常，无法解析。"


@pytest.mark.asyncio
async def test_mall_rejects_oversized_api_response_without_content_length(
    monkeypatch,
):
    response_body = json.dumps(
        {"code": 0, "data": {"name": "超大商品", "brief": "x" * 100}}
    ).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=response_body, request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall, "MAX_RESPONSE_BYTES", 64)
    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(
        ParseContext(
            text=(
                "https://mall.bilibili.com/neul-next/detailuniversal/"
                "detail.html?itemsId=13365078"
            )
        )
    )

    assert result.error == "B站会员购响应过大，无法解析。"


@pytest.mark.asyncio
async def test_mall_rejects_oversized_declared_content_length(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "65"},
            content=b"{}",
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(mall, "MAX_RESPONSE_BYTES", 64)
    monkeypatch.setattr(mall.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(
        ParseContext(
            text=(
                "https://mall.bilibili.com/neul-next/detailuniversal/"
                "detail.html?itemsId=13365078"
            )
        )
    )

    assert result.error == "B站会员购响应过大，无法解析。"
