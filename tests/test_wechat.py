import json
from types import SimpleNamespace

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.platforms.wechat import WeChatParser
from astrbot_multi_parser.platforms.wechat import parser as wechat_parser
from astrbot_multi_parser.platforms.wechat.article import parse_article_html
from astrbot_multi_parser.platforms.wechat.channels import parse_channels_payload


def install_mock_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        wechat_parser,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            )
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://mp.weixin.qq.com/s/m2Di7gZAAb00BUlhvfwr8Q",
        "https://mp.weixin.qq.com/s?__biz=test&mid=1&idx=1&sn=token",
        "https://weixin.qq.com/sph/A2pnEaFGeM",
        (
            "https://channels.weixin.qq.com/finder-preview/pages/feed"
            "?token=token-value&eid=export-id"
        ),
    ],
)
async def test_matches_supported_wechat_urls(url):
    assert await WeChatParser({}).match(ParseContext(text=url)) is True


@pytest.mark.asyncio
async def test_rejects_lookalike_wechat_url():
    context = ParseContext(
        text="https://mp.weixin.qq.com.evil.example/s/m2Di7gZAAb00BUlhvfwr8Q"
    )

    assert await WeChatParser({}).match(context) is False


def test_article_html_preserves_text_and_image_order():
    result = parse_article_html(
        """
        <meta property="og:title" content="后备标题">
        <h1 id="activity-name"><span>文章标题</span></h1>
        <span id="js_name">测试公众号</span>
        <div id="js_content">
          <p>第一段</p>
          <img data-src="//mmbiz.qpic.cn/article/640?wx_fmt=jpeg#imgIndex=1">
          <p>第二段</p>
          <script>不应出现</script>
        </div>
        """
    )

    assert result.title == "文章标题"
    assert result.author == "测试公众号"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一段"),
        ("image", "https://mmbiz.qpic.cn/article/640?wx_fmt=jpeg"),
        ("text", "第二段"),
    ]


def test_article_html_rejects_missing_public_body():
    with pytest.raises(ValueError, match="正文不可访问"):
        parse_article_html("<html><h1>安全验证</h1></html>")


@pytest.mark.asyncio
async def test_parse_article_materializes_wechat_image(
    monkeypatch,
    tmp_path,
    assert_temporary_image,
):
    article_url = "https://mp.weixin.qq.com/s/article-id"
    image_url = "https://mmbiz.qpic.cn/article/640?wx_fmt=jpeg"
    page = f"""
        <h1 id="activity-name">公众号文章</h1>
        <span id="js_name">公众号作者</span>
        <div id="js_content"><p>正文</p><img data-src="{image_url}"></div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == article_url:
            assert "cookie" not in request.headers
            return httpx.Response(200, text=page, request=request)
        assert str(request.url) == image_url
        assert request.headers["Referer"] == article_url
        return httpx.Response(
            200,
            content=b"wechat-image",
            headers={"Content-Type": "image/jpeg"},
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    parser = WeChatParser(
        {
            "image_temp_dir": str(tmp_path),
            "wechat_yuanbao_cookies": "secret=must-not-leak",
        }
    )

    result = await parser.parse(ParseContext(text=article_url))

    assert result.title == "公众号文章"
    assert result.author == "公众号作者"
    image_item = result.ordered_contents[1]
    assert_temporary_image(result, image_item.value, b"wechat-image")


@pytest.mark.asyncio
async def test_short_channels_url_requires_yuanbao_cookie(monkeypatch):
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request)
        return httpx.Response(500, request=request)

    install_mock_client(monkeypatch, handler)

    with pytest.raises(CookieAccessError, match="可能需要配置 Cookies"):
        await WeChatParser({}).parse(
            ParseContext(text="https://weixin.qq.com/sph/A2pnEaFGeM")
        )

    assert requested == []


@pytest.mark.asyncio
async def test_short_channels_url_uses_yuanbao_headers_without_leaking_them(
    monkeypatch,
):
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "yuanbao.tencent.com":
            assert "Cookie" not in request.headers
            assert request.headers["X-ID"] == "user-secret"
            assert request.headers["X-Token"] == "token-secret"
            assert json.loads(request.content) == {
                "type": "video_channel_url",
                "url": "https://weixin.qq.com/sph/A2pnEaFGeM",
                "scene": 1,
            }
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "playable_url": (
                            "https://channels.weixin.qq.com/finder-preview/pages/feed"
                            "?token=general-token&eid=export-id"
                        )
                    },
                },
                request=request,
            )

        assert request.url.host == "channels.weixin.qq.com"
        assert "cookie" not in request.headers
        assert "X-ID" not in request.headers
        assert "X-Token" not in request.headers
        assert json.loads(request.content) == {
            "baseReq": {"generalToken": "general-token"},
            "exportId": "export-id",
        }
        return httpx.Response(
            200,
            json={
                "errCode": 0,
                "data": {
                    "authorInfo": {"nickname": "视频号作者"},
                    "feedInfo": {
                        "description": "视频号标题",
                        "h264VideoInfo": {
                            "videoUrl": "https://finder.video.qq.com/video.mp4"
                        },
                        "likeCountFmt": "9257",
                    },
                },
            },
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    parser = WeChatParser(
        {
            "wechat_yuanbao_cookies": (
                "yb_user_id=user-secret; yb_token=token-secret"
            )
        }
    )

    result = await parser.parse(
        ParseContext(text="https://weixin.qq.com/sph/A2pnEaFGeM")
    )

    assert requested_hosts == [
        "yuanbao.tencent.com",
        "channels.weixin.qq.com",
    ]
    assert result.title == "视频号标题"
    assert result.author == "视频号作者"
    assert result.video_url == "https://finder.video.qq.com/video.mp4"
    assert result.extra_lines == ["赞: 9257"]


@pytest.mark.asyncio
async def test_channels_long_url_skips_yuanbao_cookie(monkeypatch):
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            200,
            json={
                "errCode": 0,
                "data": {
                    "authorInfo": {},
                    "feedInfo": {
                        "description": "长链视频",
                        "videoUrl": "https://finder.video.qq.com/long.mp4",
                    },
                },
            },
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    url = (
        "https://channels.weixin.qq.com/finder-preview/pages/feed"
        "?token=general-token&eid=export-id"
    )

    result = await WeChatParser({}).parse(ParseContext(text=url))

    assert requested_hosts == ["channels.weixin.qq.com"]
    assert result.video_url == "https://finder.video.qq.com/long.mp4"


def test_channels_payload_rejects_untrusted_video_url():
    with pytest.raises(ValueError, match="不受信任"):
        parse_channels_payload(
            {
                "data": {
                    "feedInfo": {
                        "description": "危险地址",
                        "videoUrl": "https://evil.example/video.mp4",
                    }
                }
            }
        )
