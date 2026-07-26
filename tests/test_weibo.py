from types import SimpleNamespace

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.platforms.weibo import WeiboParser
from astrbot_multi_parser.platforms.weibo import article as weibo_article
from astrbot_multi_parser.platforms.weibo import parser as weibo
from astrbot_multi_parser.platforms.weibo import post as weibo_post
from astrbot_multi_parser.platforms.weibo import video as weibo_video


def test_weibo_cookie_api_forbidden_reports_stale_cookie_without_leak():
    parser = WeiboParser({"weibo_cookies": "SUB=secret"})
    response = httpx.Response(
        403,
        request=httpx.Request("GET", "https://card.weibo.com/article/api"),
    )

    with pytest.raises(CookieAccessError, match="Cookies 可能已失效") as exc_info:
        parser.raise_for_response_status(response)

    assert "secret" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://weibo.com/7207262816/P5kWdcfDe",
        "https://m.weibo.cn/status/5234367615996775",
        "https://m.weibo.cn/detail/4976424138313924",
        "https://weibo.com/tv/show/1034:5007449447661594?mid=5007452630158934",
        "https://video.weibo.com/show?fid=1034:5145615399845897",
        "https://mapp.api.weibo.cn/fx/233911ddcc6bffea835a55e725fb0ebc.html",
        "https://weibo.com/ttarticle/p/show?id=2309404962180771742222",
        "https://card.weibo.com/article/m/show/id/2309404962180771742222",
    ],
)
async def test_matches_supported_weibo_urls(url):
    assert await WeiboParser({}).match(ParseContext(text=url))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://evilweibo.com/7207262816/P5kWdcfDe",
        "https://weibo.com.evil.example/7207262816/P5kWdcfDe",
        "https://example.com/?next=https://weibo.com.evil/1/abc",
    ],
)
async def test_rejects_lookalike_weibo_hosts(url):
    assert not await WeiboParser({}).match(ParseContext(text=url))


def test_mid_to_bid_uses_weibo_base62_chunks():
    parser = WeiboParser({})

    assert parser._base62_encode(0) == "0"
    assert parser._base62_encode(61) == "Z"
    assert parser._base62_encode(62) == "10"
    assert parser._mid_to_bid("3501756485200075") == "z0JH2lOMb"


def test_status_payload_keeps_text_images_and_repost_order():
    payload = {
        "user": {
            "id": 123,
            "screen_name": "微博作者",
            "profile_image_url": "https://tvax1.sinaimg.cn/avatar.jpg",
        },
        "bid": "P5kWdcfDe",
        "text": "正文<br />第二行 &amp; 更多",
        "status_title": "微博标题",
        "pics": [
            {
                "url": "https://wx1.sinaimg.cn/thumb/a.jpg",
                "large": {"url": "https://wx1.sinaimg.cn/large/a.jpg"},
            }
        ],
        "page_info": {
            "title": "视频标题",
            "page_pic": {"url": "https://wx1.sinaimg.cn/large/cover.jpg"},
            "urls": {
                "mp4_ld_mp4": "//f.video.weibocdn.com/low.mp4",
                "mp4_hd_mp4": "https://f.video.weibocdn.com/high.mp4",
                "mp4_720p_mp4": "https://f.video.weibocdn.com/720.mp4",
            },
        },
        "retweeted_status": {
            "user": {"id": 456, "screen_name": "原作者"},
            "bid": "AbCdEf",
            "text": "<p>原微博</p>",
            "pics": [{"large": {"url": "https://wx2.sinaimg.cn/large/b.jpg"}}],
        },
    }

    result = WeiboParser({})._parse_status_payload(payload)

    assert result.title == "视频标题"
    assert result.author == "微博作者"
    assert result.description == ""
    assert result.video_url == "https://f.video.weibocdn.com/720.mp4"
    assert result.cover_urls == ["https://wx1.sinaimg.cn/large/cover.jpg"]
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "正文\n第二行 & 更多"),
        ("image", "https://wx1.sinaimg.cn/large/a.jpg"),
        ("text", "转发自 @原作者\n原微博"),
        ("image", "https://wx2.sinaimg.cn/large/b.jpg"),
    ]


def test_status_payload_uses_repost_video_when_original_has_none():
    payload = {
        "user": {"screen_name": "转发者"},
        "text": "转发微博",
        "retweeted_status": {
            "user": {"screen_name": "视频作者"},
            "text": "原视频",
            "page_info": {
                "page_pic": {"url": "//wx1.sinaimg.cn/cover.jpg"},
                "urls": {"mp4_hd_mp4": "//f.video.weibocdn.com/repost.mp4"},
            },
        },
    }

    result = WeiboParser({})._parse_status_payload(payload)

    assert result.video_url == "https://f.video.weibocdn.com/repost.mp4"
    assert result.cover_urls == ["https://wx1.sinaimg.cn/cover.jpg"]


def test_status_payload_rejects_missing_user_data():
    with pytest.raises(ValueError, match="微博作者数据为空"):
        WeiboParser({})._parse_status_payload({"text": "没有作者"})


def install_mock_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    mocked_httpx = SimpleNamespace(
        AsyncClient=lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        )
    )
    for module in (weibo, weibo_article, weibo_post, weibo_video):
        monkeypatch.setattr(module, "httpx", mocked_httpx, raising=False)


@pytest.mark.asyncio
async def test_parse_status_uses_anonymous_mobile_api(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "m.weibo.cn"
        assert request.url.path == "/statuses/show"
        assert request.url.params["id"] == "P5kWdcfDe"
        assert "cookie" not in request.headers
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {
                    "user": {"screen_name": "微博作者"},
                    "text": "微博正文",
                },
            },
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    parser = WeiboParser({"weibo_cookies": "SUB=secret"})

    result = await parser.parse(
        ParseContext(text="https://weibo.com/7207262816/P5kWdcfDe")
    )

    assert len(requests) == 1
    assert result.author == "微博作者"
    assert result.ordered_contents[0].value == "微博正文"


@pytest.mark.asyncio
async def test_parse_tv_converts_mid_before_status_request(monkeypatch):
    requested_ids = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_ids.append(request.url.params["id"])
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {"user": {"screen_name": "作者"}, "text": "视频微博"},
            },
            request=request,
        )

    install_mock_client(monkeypatch, handler)

    await WeiboParser({}).parse(
        ParseContext(
            text=(
                "https://weibo.com/tv/show/1034:3501756485200075?mid=3501756485200075"
            )
        )
    )

    assert requested_ids == ["z0JH2lOMb"]


@pytest.mark.asyncio
async def test_parse_article_keeps_text_and_downloaded_image_order(
    monkeypatch, assert_temporary_image
):
    image_bytes = b"article-image"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "card.weibo.com":
            assert request.method == "POST"
            assert "SUB=secret" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={
                    "code": "100000",
                    "msg": "success",
                    "data": {
                        "url": "https://card.weibo.com/article/1",
                        "title": "长文章标题",
                        "content": (
                            "<p>第一段</p>"
                            '<img src="//wx1.sinaimg.cn/large/article.jpg">'
                            "<p>第二段</p>"
                        ),
                        "userinfo": {"screen_name": "文章作者"},
                    },
                },
                request=request,
            )
        assert request.url.host == "wx1.sinaimg.cn"
        assert request.headers["Referer"].startswith("https://card.weibo.com")
        return httpx.Response(200, content=image_bytes, request=request)

    install_mock_client(monkeypatch, handler)
    parser = WeiboParser({"weibo_cookies": "SUB=secret"})

    result = await parser.parse(
        ParseContext(
            text="https://card.weibo.com/article/m/show/id/2309404962180771742222"
        )
    )

    assert result.title == "长文章标题"
    assert result.author == "文章作者"
    assert [item.kind for item in result.ordered_contents] == ["text", "image", "text"]
    assert result.ordered_contents[0].value == "第一段"
    assert_temporary_image(result, result.ordered_contents[1].value, image_bytes)
    assert result.ordered_contents[2].value == "第二段"


@pytest.mark.asyncio
async def test_parse_video_page_extracts_best_available_stream(
    monkeypatch, assert_temporary_image
):
    cover_bytes = b"cover"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "h5.video.weibo.com":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "Component_Play_Playinfo": {
                            "title": "微博视频",
                            "text": "<p>视频简介</p>",
                            "cover_image": "//wx1.sinaimg.cn/cover.jpg",
                            "urls": {
                                "高清": "//f.video.weibocdn.com/high.mp4",
                                "标清": "//f.video.weibocdn.com/low.mp4",
                            },
                            "reward": {"user": {"name": "视频作者"}},
                        }
                    }
                },
                request=request,
            )
        return httpx.Response(200, content=cover_bytes, request=request)

    install_mock_client(monkeypatch, handler)

    result = await WeiboParser({}).parse(
        ParseContext(text="https://video.weibo.com/show?fid=1034:5145615399845897")
    )

    assert result.title == "微博视频"
    assert result.author == "视频作者"
    assert result.description == "视频简介"
    assert result.video_url == "https://f.video.weibocdn.com/high.mp4"
    assert_temporary_image(result, result.cover_urls[0], cover_bytes)


@pytest.mark.asyncio
async def test_parse_share_follows_only_trusted_weibo_redirect(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mapp.api.weibo.cn":
            return httpx.Response(
                302,
                headers={"Location": "https://m.weibo.cn/status/5234367615996775"},
                request=request,
            )
        if request.url.path == "/status/5234367615996775":
            return httpx.Response(200, text="share page", request=request)
        return httpx.Response(
            200,
            json={
                "ok": 1,
                "data": {"user": {"screen_name": "分享作者"}, "text": "分享正文"},
            },
            request=request,
        )

    install_mock_client(monkeypatch, handler)

    result = await WeiboParser({}).parse(
        ParseContext(
            text="https://mapp.api.weibo.cn/fx/233911ddcc6bffea835a55e725fb0ebc.html"
        )
    )

    assert result.author == "分享作者"


@pytest.mark.asyncio
async def test_parse_share_rejects_untrusted_redirect(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "mapp.api.weibo.cn":
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example/status/1"},
                request=request,
            )
        return httpx.Response(200, text="evil page", request=request)

    install_mock_client(monkeypatch, handler)

    with pytest.raises(ValueError, match="不可信域名"):
        await WeiboParser({}).parse(
            ParseContext(
                text="https://mapp.api.weibo.cn/fx/233911ddcc6bffea835a55e725fb0ebc.html"
            )
        )


@pytest.mark.asyncio
async def test_status_api_reports_risk_control_without_leaking_url(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(418, text="blocked", request=request)

    install_mock_client(monkeypatch, handler)

    with pytest.raises(ValueError, match="微博接口被风控（418）") as exc_info:
        await WeiboParser({}).parse(
            ParseContext(text="https://m.weibo.cn/status/5234367615996775")
        )

    assert "statuses/show" not in str(exc_info.value)
