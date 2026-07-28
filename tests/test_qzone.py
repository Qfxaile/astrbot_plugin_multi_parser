import json
from contextlib import asynccontextmanager
from importlib import import_module
from importlib.util import find_spec

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.platforms.qzone import QzoneParser
from astrbot_multi_parser.services.message_context import (
    _extract_json_url_and_preview,
)

VALID_URL = (
    "https://h5.qzone.qq.com/ugc/share/?sharetag=public-tag"
    "&res_uin=1725825686&cellid=9602de66438a676ad7f20000&appid=311"
)
MOBILE_ALBUM_URL = (
    "https://mobile.qzone.qq.com/l?a=4&banner_type=0"
    "&i=V52LXIoM4M3uFd0aJXj11ZiMFf0g04i7"
    "&sharetag=64760f3f-8a6f-11f1-b798-5254004610ce"
    "&u=21518887&sg=84"
)
UNIVERSAL_URL = (
    "https://h5.qzone.qq.com/universal-share/share?"
    "busi_data=%7B%22share_id%22%3A%22f1f08c3a-8a55-11f1-bf06-"
    "525400694753%22%7D&data=encrypted_share_data&svctype=1"
)


class FakeStreamResponse:
    def __init__(self, *, status_code=200, headers=None, chunks=()):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks

    async def aiter_bytes(self, chunk_size=64 * 1024):
        for chunk in self._chunks:
            yield chunk

    def raise_for_status(self):
        if self.status_code < 400:
            return
        request = httpx.Request("GET", VALID_URL)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError(
            "request failed",
            request=request,
            response=response,
        )


class FakeStreamClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def stream(self, method, url):
        self.requested_urls.append(url)
        response = self.responses.pop(0)

        @asynccontextmanager
        async def response_context():
            if isinstance(response, Exception):
                raise response
            yield response

        return response_context()


def install_fake_client(monkeypatch, responses):
    client = FakeStreamClient(responses)
    options = {}

    def client_factory(**kwargs):
        options.update(kwargs)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    return client, options


def test_qzone_platform_package_exists():
    assert find_spec("astrbot_multi_parser.platforms.qzone") is not None


def test_qzone_platform_exports_parser():
    package = import_module("astrbot_multi_parser.platforms.qzone")

    assert hasattr(package, "QzoneParser")


def test_qzone_miniapp_card_extracts_pc_url_and_preview():
    payload = {
        "meta": {
            "miniapp": {
                "jumpUrl": "mqqapi://qzoneschema/?schema=encoded",
                "legacyUrl": "https://mobile.qzone.qq.com/l?u=1&i=legacy",
                "pcJumpUrl": MOBILE_ALBUM_URL,
                "preview": "https://m.qpic.cn/preview.jpg",
            }
        }
    }

    url, preview = _extract_json_url_and_preview(json.dumps(payload))

    assert url == MOBILE_ALBUM_URL
    assert preview == "https://m.qpic.cn/preview.jpg"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (VALID_URL, True),
        (
            "https://h5.qzone.qq.com/ugc/share/?cellid=abc_123"
            "&appid=311&res_uin=123456",
            True,
        ),
        (MOBILE_ALBUM_URL, True),
        (UNIVERSAL_URL, True),
        ("https://h5.qzone.qq.com/ugc/share/?res_uin=123456", False),
        (
            "https://h5.qzone.qq.com/ugc/share/?res_uin=abc&cellid=valid",
            False,
        ),
        (
            "https://h5.qzone.qq.com.evil.example/ugc/share/?res_uin=123456&cellid=abc",
            False,
        ),
        (
            "http://h5.qzone.qq.com/ugc/share/?res_uin=123456&cellid=abc",
            False,
        ),
        (
            "https://mobile.qzone.qq.com.evil.example/l?u=123456&i=valid",
            False,
        ),
        (
            "https://h5.qzone.qq.com/universal-share/share?data=missing_busi_data",
            False,
        ),
    ],
)
async def test_qzone_match_accepts_only_valid_public_share_urls(url, expected):
    assert await QzoneParser({}).match(ParseContext(text=url)) is expected


def test_qzone_page_extracts_only_main_post_in_original_order():
    result = QzoneParser({})._parse_page(
        """
        <div class="feed dataItem">
          <div class="feed-hd"><span class="username">测试作者</span></div>
          <div class="feed-bd">
            <p class="txt">第一行<br>第二行</p>
            <div class="images ui-img-list">
              <figure><span style="background-image:url('//m.qpic.cn/a.jpg')"></span></figure>
              <figure><img data-src="https://r.photo.store.qq.com/b.jpg"></figure>
            </div>
            <video src="https://video.qq.com/c.mp4"></video>
          </div>
          <div class="feed-ft">
            <span class="username">评论者</span>
            <div class="comment-text">
              不应进入正文<img src="https://qzonestyle.gtimg.cn/em.gif">
            </div>
          </div>
        </div>
        """,
        res_uin="1725825686",
    )

    assert result.title == "QQ空间说说"
    assert result.author == "测试作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一行\n第二行"),
        ("image", "https://m.qpic.cn/a.jpg"),
        ("image", "https://r.photo.store.qq.com/b.jpg"),
    ]
    assert result.video_url == "https://video.qq.com/c.mp4"
    assert "评论" not in "".join(item.value for item in result.ordered_contents)


def test_qzone_page_falls_back_to_uin_and_filters_untrusted_media():
    result = QzoneParser({})._parse_page(
        """
        <div class="feed dataItem">
          <div class="feed-hd"><span class="username">Unknown</span></div>
          <div class="feed-bd">
            <div class="images"><img src="https://qpic.cn.evil.example/a.jpg"></div>
            <video src="https://evil.example/b.mp4"></video>
          </div>
        </div>
        """,
        res_uin="1725825686",
    )

    assert result.author == "QQ 1725825686"
    assert result.ordered_contents == []
    assert result.video_url == ""
    assert result.extra_lines == ["QQ空间说说正文为空。"]


def test_qzone_page_reports_missing_main_post():
    result = QzoneParser({})._parse_page(
        "<main>空页面</main>",
        res_uin="1725825686",
    )

    assert result.error == "未找到QQ空间说说内容，页面可能需要登录或结构已变化。"


@pytest.mark.parametrize(
    "video_markup",
    [
        '<button data-playvideo="https://video.qq.com/a.mp4"></button>',
        '<a data-video="https://video.qq.com/a.mp4"></a>',
        '<div data-videourl="https://video.qq.com/a.mp4"></div>',
    ],
)
def test_qzone_page_extracts_video_from_real_template_attributes(video_markup):
    result = QzoneParser({})._parse_page(
        f"""
        <div class="feed dataItem">
          <div class="feed-hd"><span class="username">作者</span></div>
          <div class="feed-bd">{video_markup}</div>
        </div>
        """,
        res_uin="1725825686",
    )

    assert result.video_url == "https://video.qq.com/a.mp4"


def test_qzone_page_extracts_lazy_image_from_real_template_attribute():
    result = QzoneParser({})._parse_page(
        """
        <div class="feed dataItem">
          <div class="feed-hd"><span class="username">作者</span></div>
          <div class="feed-bd">
            <div class="images"><p data-feedLazy="//m.qpic.cn/lazy.jpg"></p></div>
          </div>
        </div>
        """,
        res_uin="1725825686",
    )

    assert result.ordered_contents[0].value == "https://m.qpic.cn/lazy.jpg"


def test_qzone_album_page_extracts_direct_feed_image():
    result = QzoneParser({})._parse_page(
        """
        <div class="feed dataItem">
          <div class="feed-hd"><span class="username">相册作者</span></div>
          <div class="feed-bd">
            <img class="img" src="https://m.qpic.cn/album.jpg">
            <p class="remark"><span class="txt">上传了1张照片</span></p>
          </div>
        </div>
        """,
        res_uin="21518887",
        title="QQ空间相册",
    )

    assert result.title == "QQ空间相册"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("image", "https://m.qpic.cn/album.jpg"),
        ("text", "上传了1张照片"),
    ]


async def test_qzone_parse_requests_original_url_without_automatic_redirects(
    monkeypatch,
):
    html_text = """
    <div class="feed dataItem">
      <div class="feed-hd"><span class="username">测试作者</span></div>
      <div class="feed-bd"><p class="txt">正文</p></div>
    </div>
    """
    client, options = install_fake_client(
        monkeypatch,
        [FakeStreamResponse(chunks=(html_text.encode(),))],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.author == "测试作者"
    assert result.ordered_contents[0].value == "正文"
    assert client.requested_urls == [VALID_URL]
    assert options["follow_redirects"] is False


async def test_qzone_parse_follows_mobile_album_share(monkeypatch):
    redirected_url = (
        "https://h5.qzone.qq.com/ugc/share/?res_uin=21518887"
        "&cellid=V52LXIoM4M3uFd0aJXj11ZiMFf0g04i7&appid=4"
    )
    html_text = """
    <div class="feed dataItem">
      <div class="feed-hd"><span class="username">Unknown</span></div>
      <div class="feed-bd"><img src="https://m.qpic.cn/album.jpg"></div>
    </div>
    """
    client, _ = install_fake_client(
        monkeypatch,
        [
            FakeStreamResponse(status_code=302, headers={"Location": redirected_url}),
            FakeStreamResponse(chunks=(html_text.encode(),)),
        ],
    )
    parser = QzoneParser({})

    async def materialize_images(result, client, referer):
        return result

    monkeypatch.setattr(parser, "materialize_images", materialize_images)

    result = await parser.parse(ParseContext(text=MOBILE_ALBUM_URL))

    assert result.title == "QQ空间相册"
    assert result.author == "QQ 21518887"
    assert result.ordered_contents[0].value == "https://m.qpic.cn/album.jpg"
    assert client.requested_urls == [MOBILE_ALBUM_URL, redirected_url]


async def test_qzone_parse_reads_universal_share_nuxt_data(monkeypatch):
    template_data = {
        "nickname": "Unknown",
        "avatar": "https://q1.qlogo.cn/g?b=qq&nk=1725825686&s=640",
        "title": "",
        "album_name": "",
        "content": [{"type": 0, "content": "动态正文"}],
        "album": [
            {
                "type": 0,
                "video_url": "",
                "small_img": {"url": "https://m.qpic.cn/small.jpg"},
                "big_img": {"url": "https://m.qpic.cn/big.jpg"},
            },
            {
                "type": 1,
                "video_url": "https://video.qq.com/share.mp4",
                "small_img": {"url": "https://m.qpic.cn/cover.jpg"},
                "big_img": {"url": "https://m.qpic.cn/cover-big.jpg"},
            },
        ],
    }
    nuxt_payload = [
        ["ShallowReactive", 1],
        {"unrelated": 2},
        json.dumps(template_data, ensure_ascii=False),
    ]
    html_text = (
        '<script type="application/json" id="__NUXT_DATA__">'
        f"{json.dumps(nuxt_payload, ensure_ascii=False)}"
        "</script>"
    )
    install_fake_client(
        monkeypatch,
        [FakeStreamResponse(chunks=(html_text.encode(),))],
    )
    parser = QzoneParser({})

    async def materialize_images(result, client, referer):
        return result

    monkeypatch.setattr(parser, "materialize_images", materialize_images)

    result = await parser.parse(ParseContext(text=UNIVERSAL_URL))

    assert result.title == "QQ空间动态"
    assert result.author == "QQ 1725825686"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "动态正文"),
        ("image", "https://m.qpic.cn/big.jpg"),
    ]
    assert result.video_url == "https://video.qq.com/share.mp4"


async def test_qzone_parse_materializes_images_with_share_referer(monkeypatch):
    html_text = """
    <div class="feed dataItem">
      <div class="feed-hd"><span class="username">作者</span></div>
      <div class="feed-bd">
        <div class="images"><img src="https://m.qpic.cn/a.jpg"></div>
      </div>
    </div>
    """
    install_fake_client(
        monkeypatch,
        [FakeStreamResponse(chunks=(html_text.encode(),))],
    )
    parser = QzoneParser({})
    referers = []

    async def materialize_images(result, client, referer):
        referers.append(referer)
        return result

    monkeypatch.setattr(parser, "materialize_images", materialize_images)

    result = await parser.parse(ParseContext(text=VALID_URL))

    assert result.ordered_contents[0].value == "https://m.qpic.cn/a.jpg"
    assert referers == [VALID_URL]


@pytest.mark.parametrize("status_code", [401, 403])
async def test_qzone_parse_reports_private_or_login_required_page(
    monkeypatch,
    status_code,
):
    install_fake_client(
        monkeypatch,
        [FakeStreamResponse(status_code=status_code)],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.error == "该QQ空间说说需要登录或无权访问。"


async def test_qzone_parse_reports_deleted_page(monkeypatch):
    install_fake_client(
        monkeypatch,
        [FakeStreamResponse(status_code=404)],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.error == "该QQ空间说说已删除或不存在。"


async def test_qzone_parse_rejects_untrusted_redirect(monkeypatch):
    install_fake_client(
        monkeypatch,
        [
            FakeStreamResponse(
                status_code=302,
                headers={"Location": "https://evil.example/private"},
            )
        ],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.error == "QQ空间分享链接跳转到不可信域名。"


async def test_qzone_parse_follows_only_bounded_trusted_redirects(monkeypatch):
    redirected_url = "https://h5.qzone.qq.com/ugc/share/?res_uin=1&cellid=redirected"
    html_text = """
    <div class="feed dataItem">
      <div class="feed-hd"><span class="username">作者</span></div>
      <div class="feed-bd"><p class="txt">正文</p></div>
    </div>
    """
    client, _ = install_fake_client(
        monkeypatch,
        [
            FakeStreamResponse(status_code=302, headers={"Location": redirected_url}),
            FakeStreamResponse(chunks=(html_text.encode(),)),
        ],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert not result.error
    assert client.requested_urls == [VALID_URL, redirected_url]


@pytest.mark.parametrize(
    "response",
    [
        FakeStreamResponse(headers={"Content-Length": "6"}, chunks=(b"123456",)),
        FakeStreamResponse(chunks=(b"123", b"456")),
    ],
)
async def test_qzone_parse_rejects_oversized_page(monkeypatch, response):
    install_fake_client(monkeypatch, [response])
    parser = QzoneParser({})
    monkeypatch.setattr(parser, "MAX_PAGE_BYTES", 5)

    result = await parser.parse(ParseContext(text=VALID_URL))

    assert result.error == "QQ空间页面响应过大，已停止解析。"


async def test_qzone_parse_hides_url_when_network_fails(monkeypatch):
    request = httpx.Request("GET", VALID_URL)
    install_fake_client(
        monkeypatch,
        [httpx.ConnectError(f"failed: {VALID_URL}", request=request)],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.error == "QQ空间说说请求失败，请稍后重试。"
    assert "public-tag" not in result.error


@pytest.mark.parametrize(
    ("marker", "expected_error"),
    [
        ("该内容仅主人可见", "该QQ空间说说需要登录或无权访问。"),
        ("该内容已被删除", "该QQ空间说说已删除或不存在。"),
    ],
)
async def test_qzone_parse_maps_successful_error_pages(
    monkeypatch,
    marker,
    expected_error,
):
    install_fake_client(
        monkeypatch,
        [FakeStreamResponse(chunks=(marker.encode(),))],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.error == expected_error


async def test_qzone_parse_rejects_excessive_trusted_redirects(monkeypatch):
    redirected_url = "https://h5.qzone.qq.com/ugc/share/?res_uin=1&cellid=loop"
    install_fake_client(
        monkeypatch,
        [
            FakeStreamResponse(status_code=302, headers={"Location": redirected_url})
            for _ in range(QzoneParser.MAX_REDIRECTS + 1)
        ],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.error == "QQ空间分享链接重定向次数超过安全限制。"


async def test_qzone_parse_returns_error_when_share_url_is_missing():
    result = await QzoneParser({}).parse(ParseContext(text="没有分享链接"))

    assert result.error == "未找到QQ空间说说链接。"


async def test_qzone_parse_sets_video_download_boundaries(monkeypatch):
    html_text = """
    <div class="feed dataItem">
      <div class="feed-hd"><span class="username">作者</span></div>
      <div class="feed-bd"><video src="https://video.qq.com/a.mp4"></video></div>
    </div>
    """
    install_fake_client(
        monkeypatch,
        [FakeStreamResponse(chunks=(html_text.encode(),))],
    )

    result = await QzoneParser({}).parse(ParseContext(text=VALID_URL))

    assert result.video_download_headers == {
        "Referer": VALID_URL,
        "User-Agent": QzoneParser.HEADERS["User-Agent"],
    }
    assert result.video_download_host_suffixes == ("qq.com", "gtimg.cn")
