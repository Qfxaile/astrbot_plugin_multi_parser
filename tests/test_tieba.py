import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.platforms import tieba
from astrbot_multi_parser.platforms.tieba import parser as tieba_parser


@pytest.mark.asyncio
async def test_matches_only_tieba_thread_urls():
    parser = tieba.TiebaParser({})

    assert await parser.match(
        ParseContext(text="看看 https://tieba.baidu.com/p/1234567890?pn=2")
    )
    assert await parser.match(
        ParseContext(text="https://www.tieba.baidu.com/p/1234567890")
    )
    assert not await parser.match(
        ParseContext(text="https://tieba.baidu.com/f?kw=python")
    )
    assert not await parser.match(
        ParseContext(text="https://tieba.baidu.com.evil.example/p/1234567890")
    )
    assert not await parser.match(
        ParseContext(text="https://tieba.baidu.com/p/1234567890invalid")
    )


def test_parse_page_keeps_first_post_text_image_order_and_video():
    html = """
    <html>
      <h1 class="core_title_txt" title="示例帖子标题">忽略的标题文本</h1>
      <div class="l_post j_l_post" data-field='{"author":{"user_name":"楼主"}}'>
        <div class="d_post_content j_d_post_content">
          <p>第一段正文</p>
          <img class="BDE_Image"
               src="https://tiebapic.baidu.com/forum/pic/item/first.jpg">
          <div>第二段正文</div>
          <img src="https://tiebapic.baidu.com/forum/pic/item/first.jpg">
          <video src="https://video-tieba.cdn.bcebos.com/example.mp4"></video>
        </div>
      </div>
      <div class="l_post j_l_post" data-field='{"author":{"user_name":"回复者"}}'>
        <div class="d_post_content j_d_post_content">
          <p>不应解析的回复</p>
          <img src="https://tiebapic.baidu.com/forum/pic/item/reply.jpg">
        </div>
      </div>
    </html>
    """

    result = tieba.TiebaParser({})._parse_page(html, "1234567890")

    assert result.title == "示例帖子标题"
    assert result.author == "楼主"
    assert result.video_url == "https://video-tieba.cdn.bcebos.com/example.mp4"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一段正文"),
        ("image", "https://tiebapic.baidu.com/forum/pic/item/first.jpg"),
        ("text", "第二段正文"),
        ("image", "https://tiebapic.baidu.com/forum/pic/item/first.jpg"),
    ]


def test_parse_page_uses_visible_author_and_protocol_relative_image_fallbacks():
    html = """
    <h1 class="core_title_txt">无属性标题</h1>
    <div class="l_post j_l_post" data-field="not-json">
      <a class="p_author_name">可见作者</a>
      <div class="d_post_content j_d_post_content">
        正文
        <img data-original="//imgsa.baidu.com/forum/pic/item/fallback.png">
      </div>
    </div>
    """

    result = tieba.TiebaParser({})._parse_page(html, "123")

    assert result.title == "无属性标题"
    assert result.author == "可见作者"
    assert result.ordered_contents[-1].value == (
        "https://imgsa.baidu.com/forum/pic/item/fallback.png"
    )


@pytest.mark.parametrize(
    ("cookie_header", "expected"),
    [
        ("", "TIEBA_NEW_PC=0"),
        (
            "BDUSS=secret; TIEBA_NEW_PC=1; STOKEN=token=value; undefined",
            "BDUSS=secret; TIEBA_NEW_PC=0; STOKEN=token=value",
        ),
        (
            "TIEBA_NEW_PC=1; BAIDUID=id; TIEBA_NEW_PC=1",
            "TIEBA_NEW_PC=0; BAIDUID=id",
        ),
    ],
)
def test_legacy_page_cookie_header_forces_old_pc_page(cookie_header, expected):
    parser = tieba.TiebaParser({"tieba_cookies": cookie_header})

    assert parser._legacy_page_cookie_header() == expected


@pytest.mark.parametrize(
    ("html", "expected_error"),
    [
        (
            "<title>百度安全验证</title><script>window.BIOC_OPTIONS={}</script>",
            "配置 Cookies",
        ),
        ("<div>抱歉，该贴已被删除</div>", "已被删除"),
        ("<div>抱歉，根据相关法律法规和政策，本吧暂不开放</div>", "无法访问"),
    ],
)
def test_parse_page_returns_readable_platform_errors(html, expected_error):
    result = tieba.TiebaParser({})._parse_page(html, "123")

    assert expected_error in result.error


@pytest.mark.asyncio
async def test_parse_materializes_images_without_leaking_tieba_cookies(
    monkeypatch, assert_temporary_image
):
    page_url = "https://tieba.baidu.com/p/123"
    image_url = "https://tiebapic.baidu.com/forum/pic/item/first.jpg"
    requests = []
    page_html = f"""
    <h1 class="core_title_txt">测试帖子</h1>
    <div class="l_post j_l_post" data-field='{{"author":{{"user_name":"楼主"}}}}'>
      <div class="d_post_content j_d_post_content">
        <p>正文</p><img src="{image_url}">
      </div>
    </div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "tieba.baidu.com":
            return httpx.Response(200, text=page_html)
        if request.url.host == "tiebapic.baidu.com":
            return httpx.Response(200, content=b"image-bytes")
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(tieba_parser.httpx, "AsyncClient", lambda **kwargs: client)

    result = await tieba.TiebaParser(
        {
            "cookies": {
                "tieba_cookies": (
                    "BAIDUID=device-secret; BIDUPSID=browser-secret; "
                    "PSTM=login-time; BDUSS=session-secret; TIEBA_NEW_PC=1"
                )
            },
            "request_timeout_seconds": 12,
        }
    ).parse(ParseContext(text=page_url))

    assert_temporary_image(result, result.ordered_contents[-1].value, b"image-bytes")
    page_request, image_request = requests
    assert page_request.url.params["see_lz"] == "1"
    assert page_request.url.params["pn"] == "1"
    assert page_request.headers["Cookie"] == (
        "BAIDUID=device-secret; BIDUPSID=browser-secret; PSTM=login-time; "
        "BDUSS=session-secret; TIEBA_NEW_PC=0"
    )
    assert image_request.headers["Referer"] == page_url
    assert "Cookie" not in image_request.headers


@pytest.mark.asyncio
async def test_parse_returns_error_when_thread_link_is_missing():
    result = await tieba.TiebaParser({}).parse(ParseContext(text="没有链接"))

    assert result.error == "未找到贴吧帖子链接。"
