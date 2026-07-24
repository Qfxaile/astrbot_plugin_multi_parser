import json
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
from astrbot_multi_parser.core.contracts import OrderedContent, ParseContext
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.platforms.zhihu import content as zhihu_content
from astrbot_multi_parser.platforms.zhihu import request as zhihu_request
from astrbot_multi_parser.platforms.zhihu.common import (
    merge_unique_urls,
    normalize_media_url,
    normalize_text,
)
from astrbot_multi_parser.platforms.zhihu.content import (
    extract_html_video_urls,
    parse_html_body,
)
from astrbot_multi_parser.platforms.zhihu.handlers import (
    parse_answer_payload,
    parse_article_payload,
    parse_pin_payload,
    parse_question_payload,
)


@pytest.mark.asyncio
async def test_zhihu_page_forbidden_reports_missing_cookie():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        requester = zhihu_request.ZhihuRequest({})
        with pytest.raises(CookieAccessError, match="可能需要配置 Cookies"):
            await requester.get_page(client, "https://www.zhihu.com/question/1")


def test_answer_content_is_parsed_only_once(monkeypatch):
    feed_calls = 0
    original_feed = zhihu_content.ZhihuHTMLParser.feed

    def tracking_feed(self, data):
        nonlocal feed_calls
        feed_calls += 1
        return original_feed(self, data)

    monkeypatch.setattr(zhihu_content.ZhihuHTMLParser, "feed", tracking_feed)

    result = parse_answer_payload(
        {
            "question": {"title": "问题"},
            "author": {"name": "答主"},
            "content": (
                '<p>正文</p><img data-original="https://picx.zhimg.com/a.jpg">'
                '<video src="https://video.zhihu.com/a.mp4"></video>'
            ),
        }
    )

    assert feed_calls == 1
    assert result.video_url == "https://video.zhihu.com/a.mp4"
    assert [item.kind for item in result.ordered_contents] == ["text", "image"]


def test_normalize_text_decodes_entities_and_compacts_whitespace():
    assert normalize_text(
        "  第一段&nbsp; 内容\r\n\r\n\r\n第二段  ", keep_newlines=True
    ) == ("第一段 内容\n\n第二段")


@pytest.mark.parametrize(
    ("value", "page_url", "expected"),
    [
        ("//picx.zhimg.com/a.jpg", None, "https://picx.zhimg.com/a.jpg"),
        (
            "/video/a.mp4",
            "https://www.zhihu.com/question/1",
            "https://www.zhihu.com/video/a.mp4",
        ),
        ("javascript:alert(1)", None, ""),
        ("data:image/png;base64,AAAA", None, ""),
    ],
)
def test_normalize_media_url_accepts_only_http_urls(value, page_url, expected):
    assert normalize_media_url(value, page_url) == expected


def test_merge_unique_urls_ignores_query_for_same_media():
    assert merge_unique_urls(
        ["https://picx.zhimg.com/a.jpg?source=1"],
        [
            "http://picx.zhimg.com/a.jpg?source=2",
            "https://picx.zhimg.com/b.jpg",
        ],
    ) == [
        "https://picx.zhimg.com/a.jpg?source=1",
        "https://picx.zhimg.com/b.jpg",
    ]


def test_html_body_keeps_text_and_image_order():
    body = (
        "<p>第一段</p>"
        '<figure><img data-original="//picx.zhimg.com/a.jpg"></figure>'
        "<p>第二段<br>下一行</p>"
    )

    blocks = parse_html_body(body)

    assert blocks == [
        OrderedContent(kind="text", value="第一段"),
        OrderedContent(kind="image", value="https://picx.zhimg.com/a.jpg"),
        OrderedContent(kind="text", value="第二段"),
        OrderedContent(kind="text", value="下一行"),
    ]


def test_html_body_filters_hidden_nodes_and_preserves_duplicate_images():
    body = (
        "<style>隐藏样式</style><script>隐藏脚本</script>"
        "<p>正文</p>"
        '<img src="https://pic1.zhimg.com/a.jpg?x=1">'
        '<img data-original="https://pic1.zhimg.com/a.jpg?x=2">'
    )

    blocks = parse_html_body(body)

    assert blocks == [
        OrderedContent(kind="text", value="正文"),
        OrderedContent(kind="image", value="https://pic1.zhimg.com/a.jpg?x=1"),
        OrderedContent(kind="image", value="https://pic1.zhimg.com/a.jpg?x=2"),
    ]


def test_extract_html_video_urls_uses_source_and_link_candidates():
    body = (
        '<video src="https://video.zhihu.com/a.mp4"></video>'
        '<a data-video-id="2" href="https://video.zhihu.com/b.m3u8">视频</a>'
        '<source src="https://video.zhihu.com/a.mp4?duplicate=1">'
    )

    assert extract_html_video_urls(body) == [
        "https://video.zhihu.com/a.mp4",
        "https://video.zhihu.com/b.m3u8",
    ]


@pytest.mark.asyncio
async def test_placeholder_parser_still_matches_question_url():
    from astrbot_multi_parser.platforms import ZhihuParser

    assert await ZhihuParser({}).match(
        ParseContext(text="https://www.zhihu.com/question/123")
    )


def test_answer_payload_builds_author_stats_and_ordered_body():
    result = parse_answer_payload(
        {
            "question": {"title": "问题标题"},
            "author": {"name": "答主"},
            "content": (
                "<p>回答正文</p>"
                '<img src="https://picx.zhimg.com/answer.jpg">'
                '<video src="https://video.zhihu.com/answer.mp4"></video>'
            ),
            "voteupCount": 12,
            "commentCount": 3,
            "favoriteCount": 2,
        }
    )

    assert result.title == "问题标题"
    assert result.author == "答主"
    assert result.extra_lines == ["赞同 12 | 评论 3 | 收藏 2"]
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "回答正文"),
        ("image", "https://picx.zhimg.com/answer.jpg"),
    ]
    assert result.video_url == "https://video.zhihu.com/answer.mp4"


def test_question_payload_appends_default_first_answer():
    result = parse_question_payload(
        {
            "title": "问题标题",
            "detail": "<p>问题描述</p>",
            "author": {"name": "提问者"},
            "answerCount": 5,
            "followerCount": 12000,
            "visitCount": 100000000,
        },
        {
            "author": {"name": "首答作者"},
            "content": "<p>首条回答</p>",
        },
    )

    assert result.title == "问题标题"
    assert result.author == "首答作者"
    assert result.extra_lines == ["回答 5 | 关注 1.2万 | 浏览 1亿"]
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "问题描述"),
        ("text", "默认排序首条回答 @首答作者"),
        ("text", "首条回答"),
    ]


def test_article_payload_uses_article_title_and_multiple_video_policy():
    result = parse_article_payload(
        {
            "title": "专栏标题",
            "author": {"name": "文章作者"},
            "content": (
                "<p>文章正文</p>"
                '<video src="https://video.zhihu.com/first.mp4"></video>'
                '<video src="https://video.zhihu.com/second.mp4"></video>'
            ),
            "voteupCount": 20000,
            "commentCount": 4,
        }
    )

    assert result.title == "专栏标题"
    assert result.author == "文章作者"
    assert result.video_url == "https://video.zhihu.com/first.mp4"
    assert result.ordered_contents[-1] == OrderedContent(
        kind="text",
        value="视频链接: https://video.zhihu.com/second.mp4",
    )
    assert result.extra_lines == ["赞同 2万 | 评论 4"]


def test_pin_payload_handles_structured_text_image_and_video():
    result = parse_pin_payload(
        {
            "author": {"name": "想法作者"},
            "content": [
                {"type": "text", "content": "想法正文"},
                {
                    "type": "image",
                    "url": "https://pic1.zhimg.com/pin-thumbnail.jpg",
                    "original_url": "https://pic1.zhimg.com/pin-original.jpg",
                },
                {
                    "type": "image",
                    "original_url": "https://pic1.zhimg.com/pin-original.jpg",
                },
                {
                    "type": "video",
                    "video": {
                        "playlist": {
                            "hd": {"playUrl": "https://video.zhihu.com/pin.mp4"}
                        }
                    },
                },
            ],
            "voteup_count": 8,
            "comment_count": 1,
        }
    )

    assert result.title == "知乎想法"
    assert result.author == "想法作者"
    assert result.ordered_contents == [
        OrderedContent(kind="text", value="想法正文"),
        OrderedContent(kind="image", value="https://pic1.zhimg.com/pin-original.jpg"),
        OrderedContent(kind="image", value="https://pic1.zhimg.com/pin-original.jpg"),
    ]
    assert result.video_url == "https://video.zhihu.com/pin.mp4"
    assert result.extra_lines == ["赞同 8 | 评论 1"]


@pytest.mark.parametrize(
    ("handler", "payload", "message"),
    [
        (parse_answer_payload, {}, "知乎回答数据为空"),
        (parse_article_payload, {}, "知乎文章数据为空"),
        (parse_pin_payload, {}, "知乎想法数据为空"),
    ],
)
def test_handlers_reject_empty_payloads(handler, payload, message):
    with pytest.raises(ValueError, match=message):
        handler(payload)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://www.zhihu.com/question/1",
        "https://www.zhihu.com/question/1/answer/2",
        "https://zhuanlan.zhihu.com/p/3",
        "https://www.zhihu.com/pin/4",
        "https://www.zhihu.com/tardis/zm/art/5",
        "https://link.zhihu.com/?target=https%3A%2F%2Fwww.zhihu.com%2Fquestion%2F1",
    ],
)
async def test_matches_supported_zhihu_urls(url):
    from astrbot_multi_parser.platforms import ZhihuParser

    assert await ZhihuParser({}).match(ParseContext(text=url))


@pytest.mark.asyncio
async def test_rejects_lookalike_zhihu_url():
    from astrbot_multi_parser.platforms import ZhihuParser

    assert not await ZhihuParser({}).match(
        ParseContext(text="https://zhihu.com.evil.example/question/1")
    )


def install_zhihu_mock_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        zhihu_request,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(handler), **kwargs
            )
        ),
        raising=False,
    )


@pytest.mark.asyncio
async def test_parse_answer_uses_cookie_only_for_zhihu_and_materializes_image(
    monkeypatch, assert_temporary_image
):
    from astrbot_multi_parser.platforms import ZhihuParser

    image_bytes = b"answer-image"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.zhihu.com":
            assert request.url.path == "/api/v4/answers/2"
            assert request.url.params["include"] == "content"
            assert "z_c0=secret" in request.headers.get("cookie", "")
            return httpx.Response(
                200,
                json={
                    "question": {"title": "接口问题"},
                    "author": {"name": "接口答主"},
                    "content": '<img src="https://picx.zhimg.com/a.jpg">',
                },
                request=request,
            )
        assert request.url.host == "picx.zhimg.com"
        assert "cookie" not in request.headers
        return httpx.Response(200, content=image_bytes, request=request)

    install_zhihu_mock_client(monkeypatch, handler)
    result = await ZhihuParser({"zhihu_cookies": "z_c0=secret"}).parse(
        ParseContext(text="https://www.zhihu.com/question/1/answer/2")
    )

    assert result.title == "接口问题"
    assert result.author == "接口答主"
    assert_temporary_image(result, result.ordered_contents[0].value, image_bytes)


@pytest.mark.asyncio
async def test_parse_question_fetches_default_first_answer(monkeypatch):
    from astrbot_multi_parser.platforms import ZhihuParser

    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/api/v4/questions/1":
            return httpx.Response(
                200,
                json={"title": "接口问题", "detail": "<p>问题描述</p>"},
                request=request,
            )
        assert request.url.params["include"] == "data[*].content"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "author": {"name": "首答作者"},
                        "content": "<p>首条回答</p>",
                    }
                ]
            },
            request=request,
        )

    install_zhihu_mock_client(monkeypatch, handler)
    result = await ZhihuParser({}).parse(
        ParseContext(text="https://www.zhihu.com/question/1")
    )

    assert requested_paths == [
        "/api/v4/questions/1",
        "/api/v4/questions/1/answers",
    ]
    assert result.author == "首答作者"
    assert result.ordered_contents[-1].value == "首条回答"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "api_path", "payload", "expected_title"),
    [
        (
            "https://www.zhihu.com/tardis/zm/art/5",
            "/api/v4/articles/5",
            {"title": "移动文章", "author": {"name": "作者"}, "content": "正文"},
            "移动文章",
        ),
        (
            "https://www.zhihu.com/pin/4",
            "/api/v4/pins/4",
            {
                "author": {"name": "作者"},
                "content": [{"type": "text", "content": "想法"}],
            },
            "知乎想法",
        ),
    ],
)
async def test_parse_routes_article_and_pin(
    monkeypatch, url, api_path, payload, expected_title
):
    from astrbot_multi_parser.platforms import ZhihuParser

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == api_path
        return httpx.Response(200, json=payload, request=request)

    install_zhihu_mock_client(monkeypatch, handler)

    result = await ZhihuParser({}).parse(ParseContext(text=url))

    assert result.title == expected_title


@pytest.mark.asyncio
async def test_parse_share_follows_trusted_redirect(monkeypatch):
    from astrbot_multi_parser.platforms import ZhihuParser

    target = "https://www.zhihu.com/question/1/answer/2"
    share_url = f"https://link.zhihu.com/?target={quote(target, safe='')}"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "link.zhihu.com":
            return httpx.Response(302, headers={"Location": target}, request=request)
        if request.url.path == "/question/1/answer/2":
            return httpx.Response(200, text="answer page", request=request)
        return httpx.Response(
            200,
            json={
                "question": {"title": "跳转问题"},
                "author": {"name": "跳转答主"},
                "content": "回答",
            },
            request=request,
        )

    install_zhihu_mock_client(monkeypatch, handler)
    result = await ZhihuParser({}).parse(ParseContext(text=share_url))

    assert result.title == "跳转问题"


@pytest.mark.asyncio
async def test_parse_share_rejects_untrusted_redirect(monkeypatch):
    from astrbot_multi_parser.platforms import ZhihuParser

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "link.zhihu.com":
            return httpx.Response(
                302,
                headers={"Location": "https://evil.example/question/1"},
                request=request,
            )
        return httpx.Response(200, text="evil", request=request)

    install_zhihu_mock_client(monkeypatch, handler)

    with pytest.raises(ValueError, match="不可信域名"):
        await ZhihuParser({}).parse(
            ParseContext(text="https://link.zhihu.com/?target=ignored")
        )


@pytest.mark.asyncio
async def test_answer_api_risk_control_falls_back_to_initial_state(monkeypatch):
    from astrbot_multi_parser.platforms import ZhihuParser

    answer = {
        "question": {"title": "页面问题"},
        "author": {"name": "页面答主"},
        "content": "<p>页面回答</p>",
    }
    state = {"initialState": {"entities": {"answers": {"2": answer}}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/answers/2":
            return httpx.Response(403, text="blocked", request=request)
        return httpx.Response(
            200,
            text=(
                '<script id="js-initialData" type="application/json">'
                f"{json.dumps(state)}"
                "</script>"
            ),
            request=request,
        )

    install_zhihu_mock_client(monkeypatch, handler)
    result = await ZhihuParser({}).parse(
        ParseContext(text="https://www.zhihu.com/question/1/answer/2")
    )

    assert result.title == "页面问题"
    assert result.author == "页面答主"


@pytest.mark.asyncio
async def test_answer_api_without_content_falls_back_to_initial_state(monkeypatch):
    from astrbot_multi_parser.platforms import ZhihuParser

    answer = {
        "question": {"title": "页面问题"},
        "author": {"name": "页面答主"},
        "content": "<p>完整回答正文</p>",
    }
    state = {"initialState": {"entities": {"answers": {"2": answer}}}}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v4/answers/2":
            assert request.url.params["include"] == "content"
            return httpx.Response(
                200,
                json={
                    "question": {"title": "接口问题"},
                    "author": {"name": "接口答主"},
                    "excerpt": "回答摘要",
                },
                request=request,
            )
        return httpx.Response(
            200,
            text=(
                '<script id="js-initialData" type="application/json">'
                f"{json.dumps(state)}"
                "</script>"
            ),
            request=request,
        )

    install_zhihu_mock_client(monkeypatch, handler)
    result = await ZhihuParser({}).parse(
        ParseContext(text="https://www.zhihu.com/question/1/answer/2")
    )

    assert result.title == "页面问题"
    assert result.author == "页面答主"
    assert result.ordered_contents == [
        OrderedContent(kind="text", value="完整回答正文")
    ]
