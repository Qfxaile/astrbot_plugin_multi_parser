import httpx
import pytest
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.models import ParseContext, ParseResult
from astrbot_multi_parser.platforms.bilibili import parser as bilibili


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, "可能需要配置 Cookies"),
        ({"bilibili_cookies": "SESSDATA=secret"}, "Cookies 可能已失效"),
    ],
)
def test_bilibili_auth_business_code_reports_cookie_hint(config, expected):
    parser = bilibili.BilibiliParser(config)

    with pytest.raises(CookieAccessError, match=expected) as exc_info:
        parser._parse_dynamic_payload({"code": -101, "message": "账号未登录"})

    assert "secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_short_video_url_extracts_id_when_redirect_target_returns_412(
    monkeypatch,
):
    parser = bilibili.BilibiliParser({"bilibili_cookies": "SESSDATA=secret"})
    short_url = "https://b23.tv/dBRgvHl"
    shared_url = (
        "https://www.bilibili.com/video/BV1VpK56jERg"
        "?buvid=test&share_source=QQ&spmid=united.player-video-detail.0.0"
    )
    requested_urls = []
    requested_cookies = []
    parsed_video_ids = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        requested_cookies.append(request.headers.get("Cookie"))
        if str(request.url) == short_url:
            return httpx.Response(
                302,
                headers={"Location": shared_url},
                request=request,
            )
        return httpx.Response(412, request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    async def get_video_info(video_id):
        parsed_video_ids.append(video_id)
        return {
            "title": "视频标题",
            "author": "视频作者",
            "desc": "",
            "cid": "1",
            "pic": "",
        }

    async def get_play_url(cid, video_id):
        return ""

    monkeypatch.setattr(bilibili.httpx, "AsyncClient", create_client)
    monkeypatch.setattr(parser, "_get_video_info", get_video_info)
    monkeypatch.setattr(parser, "_get_play_url", get_play_url)

    result = await parser.parse(ParseContext(text=short_url))

    assert result.title == "视频标题"
    assert parsed_video_ids == ["BV1VpK56jERg"]
    assert requested_urls == [short_url, shared_url]
    assert requested_cookies == [None, None]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://i0.hdslb.com/bfs/new_dyn/image.jpg@672w_378h_1c.webp",
            "https://i0.hdslb.com/bfs/new_dyn/image.jpg",
        ),
        (
            "//i0.hdslb.com/bfs/article/image.png@!web-article-pic.avif",
            "https://i0.hdslb.com/bfs/article/image.png",
        ),
        (
            "https://img.example/user@2x/image.jpg",
            "https://img.example/user@2x/image.jpg",
        ),
        (
            "https://evilhdslb.com/image.jpg@672w.webp",
            "https://evilhdslb.com/image.jpg@672w.webp",
        ),
        (
            "https://hdslb.com.evil/image.jpg@672w.webp",
            "https://hdslb.com.evil/image.jpg@672w.webp",
        ),
        (
            "https://user:pass@i0.hdslb.com:443/image.jpg@672w.webp?token=a@b#part@2",
            "https://user:pass@i0.hdslb.com:443/image.jpg?token=a@b#part@2",
        ),
        (
            "//user:pass@i0.hdslb.com:443/image.jpg@672w.webp?token=a@b#part@2",
            "https://user:pass@i0.hdslb.com:443/image.jpg?token=a@b#part@2",
        ),
        (
            "https://[invalid/image.jpg@672w.webp",
            "https://[invalid/image.jpg@672w.webp",
        ),
        (
            "https://i0.hdslb.com:bad/image.jpg@672w.webp",
            "https://i0.hdslb.com:bad/image.jpg@672w.webp",
        ),
        (
            "https://i0.hdslb.com:70000/image.jpg@672w.webp",
            "https://i0.hdslb.com:70000/image.jpg@672w.webp",
        ),
        (
            "https://i0.hdslb.com:8443/image.jpg@672w.webp",
            "https://i0.hdslb.com:8443/image.jpg",
        ),
        (
            "https://i0.hdslb.com/user@2x/image.jpg",
            "https://i0.hdslb.com/user@2x/image.jpg",
        ),
        (
            "https://i0.hdslb.com/user@2x/image.jpg@1048w_!web-dynamic.avif",
            "https://i0.hdslb.com/user@2x/image.jpg",
        ),
        (
            "https://i0.hdslb.com/image.jpg@not-a-transform",
            "https://i0.hdslb.com/image.jpg@not-a-transform",
        ),
    ],
)
def test_original_image_url_only_removes_hdslb_transform(url, expected):
    assert bilibili._original_image_url(url) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://t.bilibili.com/123",
        "https://www.bilibili.com/dynamic/123",
        "https://www.bilibili.com/opus/456",
        "https://www.bilibili.com/read/cv789",
    ],
)
async def test_matches_bilibili_graphic_urls(url):
    parser = bilibili.BilibiliParser({"request_timeout_seconds": 30})

    assert await parser.match(ParseContext(text=url))


@pytest.mark.asyncio
async def test_matches_bilibili_live_url():
    parser = bilibili.BilibiliParser({})

    assert await parser.match(
        ParseContext(text="https://live.bilibili.com/123456?broadcast_type=0")
    )


def test_live_payload_extracts_room_information():
    payload = {
        "code": 0,
        "data": {
            "room_info": {
                "room_id": 123456,
                "title": "直播标题",
                "live_status": 1,
                "parent_area_name": "游戏",
                "area_name": "单机游戏",
                "online": 12345,
                "keyframe": "https://i0.hdslb.com/live.jpg@672w.webp",
            },
            "anchor_info": {"base_info": {"uname": "主播昵称"}},
        },
    }

    result = bilibili.BilibiliParser({})._parse_live_payload(payload)

    assert result.title == "直播标题"
    assert result.author == "主播昵称"
    assert result.cover_urls == ["https://i0.hdslb.com/live.jpg"]
    assert result.extra_lines == [
        "直播状态: 直播中",
        "分区: 游戏 / 单机游戏",
        "人气: 12,345",
    ]


@pytest.mark.asyncio
async def test_parse_live_requests_api_and_materializes_cover(
    monkeypatch, assert_temporary_image
):
    live_url = "https://live.bilibili.com/123456"
    cover_url = "https://i0.hdslb.com/live-cover.jpg"
    api_request = None
    image_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_request, image_request
        if request.url.host == "api.live.bilibili.com":
            api_request = request
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "room_info": {
                            "room_id": 123456,
                            "title": "直播标题",
                            "live_status": 1,
                            "keyframe": cover_url,
                        },
                        "anchor_info": {"base_info": {"uname": "主播"}},
                    },
                },
                request=request,
            )
        image_request = request
        return httpx.Response(200, content=b"live-cover", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        bilibili.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await bilibili.BilibiliParser(
        {"bilibili_cookies": "SESSDATA=live-session"}
    ).parse(ParseContext(text=live_url))

    assert result.title == "直播标题"
    assert_temporary_image(result, result.cover_urls[0], b"live-cover")
    assert api_request is not None
    assert api_request.url.params["room_id"] == "123456"
    assert "SESSDATA=live-session" in api_request.headers["Cookie"]
    assert image_request is not None
    assert image_request.headers["Referer"] == live_url
    assert "Cookie" not in image_request.headers


def test_dynamic_payload_extracts_text_and_images_in_order():
    payload = {
        "code": 0,
        "data": {
            "item": {
                "modules": {
                    "module_author": {"name": "动态作者"},
                    "module_dynamic": {
                        "desc": {"text": "动态正文"},
                        "major": {
                            "type": "MAJOR_TYPE_OPUS",
                            "opus": {
                                "title": "动态标题",
                                "pics": [
                                    {
                                        "url": "https://i0.hdslb.com/dynamic.jpg@672w.webp"
                                    }
                                ],
                            },
                        },
                    },
                }
            }
        },
    }

    result = bilibili.BilibiliParser({})._parse_dynamic_payload(payload)

    assert result.title == "动态标题"
    assert result.author == "动态作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "动态正文"),
        ("image", "https://i0.hdslb.com/dynamic.jpg"),
    ]


def test_dynamic_archive_uses_original_cover_url():
    payload = {
        "code": 0,
        "data": {
            "item": {
                "modules": {
                    "module_author": {"name": "动态作者"},
                    "module_dynamic": {
                        "major": {
                            "type": "MAJOR_TYPE_ARCHIVE",
                            "archive": {
                                "title": "视频标题",
                                "cover": "//i0.hdslb.com/archive.jpg@672w.webp",
                            },
                        }
                    },
                }
            }
        },
    }

    result = bilibili.BilibiliParser({})._parse_dynamic_payload(payload)

    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("image", "https://i0.hdslb.com/archive.jpg")
    ]


def test_dynamic_article_uses_article_description_and_covers():
    payload = {
        "code": 0,
        "data": {
            "item": {
                "modules": {
                    "module_author": {"name": "专栏作者"},
                    "module_dynamic": {
                        "desc": None,
                        "major": {
                            "type": "MAJOR_TYPE_ARTICLE",
                            "article": {
                                "title": "传统专栏标题",
                                "desc": "传统专栏摘要",
                                "covers": [
                                    "//i0.hdslb.com/article-cover.jpg@672w.webp"
                                ],
                            },
                        },
                    },
                }
            }
        },
    }

    result = bilibili.BilibiliParser({})._parse_dynamic_payload(payload)

    assert result.title == "传统专栏标题"
    assert result.author == "专栏作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "传统专栏摘要"),
        ("image", "https://i0.hdslb.com/article-cover.jpg"),
    ]


def test_opus_payload_keeps_paragraph_order():
    payload = {
        "code": 0,
        "data": {
            "item": {
                "basic": {"title": "图文标题"},
                "modules": [
                    {
                        "module_type": "MODULE_TYPE_AUTHOR",
                        "module_author": {"name": "图文作者"},
                    },
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {
                                    "text": {
                                        "nodes": [
                                            {
                                                "type": "TEXT_NODE_TYPE_WORD",
                                                "word": {"words": "第一段"},
                                            }
                                        ]
                                    },
                                    "pic": None,
                                },
                                {
                                    "text": None,
                                    "pic": {
                                        "pics": [
                                            {
                                                "url": "//i0.hdslb.com/opus.jpg@!web-comment-note.avif"
                                            }
                                        ]
                                    }
                                },
                                {
                                    "text": {
                                        "nodes": [
                                            {
                                                "type": "TEXT_NODE_TYPE_RICH",
                                                "rich": {"text": "第二段"},
                                            }
                                        ]
                                    },
                                    "pic": None,
                                },
                            ]
                        },
                    },
                ],
            }
        },
    }

    result = bilibili.BilibiliParser({})._parse_opus_payload(payload)

    assert result.title == "图文标题"
    assert result.author == "图文作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一段"),
        ("image", "https://i0.hdslb.com/opus.jpg"),
        ("text", "第二段"),
    ]


def test_opus_payload_extracts_top_album_images():
    payload = {
        "code": 0,
        "data": {
            "item": {
                "basic": {"title": "顶部相册图文"},
                "modules": [
                    {
                        "module_type": "MODULE_TYPE_TOP",
                        "module_top": {
                            "display": {
                                "album": {
                                    "pics": [
                                        {
                                            "url": "http://i0.hdslb.com/top-1.jpg@672w.webp"
                                        },
                                        None,
                                        {
                                            "url": "//i0.hdslb.com/top-2.jpg@!web-comment-note.avif"
                                        },
                                    ]
                                }
                            }
                        },
                    },
                    {
                        "module_type": "MODULE_TYPE_CONTENT",
                        "module_content": {
                            "paragraphs": [
                                {
                                    "text": {
                                        "nodes": [
                                            {
                                                "type": "TEXT_NODE_TYPE_WORD",
                                                "word": {"words": "正文"},
                                            }
                                        ]
                                    },
                                    "pic": None,
                                }
                            ]
                        },
                    },
                ],
            }
        },
    }

    result = bilibili.BilibiliParser({})._parse_opus_payload(payload)

    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("image", "http://i0.hdslb.com/top-1.jpg"),
        ("image", "https://i0.hdslb.com/top-2.jpg"),
        ("text", "正文"),
    ]


def test_article_html_keeps_visible_text_and_image_order():
    html = """
    <html>
      <head>
        <meta property="og:title" content="专栏标题">
        <meta name="author" content="专栏作者">
      </head>
      <body>
        <div class="article-holder">
          <p>第一段</p>
          <figure><img data-src="//i0.hdslb.com/article.jpg@!web-article-pic.avif"></figure>
          <p>第二段</p>
        </div>
      </body>
    </html>
    """

    result = bilibili.BilibiliParser({})._parse_article_html(html)

    assert result.title == "专栏标题"
    assert result.author == "专栏作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一段"),
        ("image", "https://i0.hdslb.com/article.jpg"),
        ("text", "第二段"),
    ]


def test_article_payload_keeps_full_content_and_image_order():
    payload = {
        "code": 0,
        "data": {
            "title": "传统专栏标题",
            "author": {"name": "专栏作者"},
            "origin_image_urls": [
                "//i0.hdslb.com/article-cover.jpg@672w.webp"
            ],
            "content": (
                '<img data-src="//i0.hdslb.com/article-cover.jpg@672w.webp">'
                "<p>完整正文第一段</p>"
                '<figure><img data-src="//i0.hdslb.com/content.jpg@672w.webp"></figure>'
                "<p>完整正文第二段</p>"
            ),
        },
    }

    result = bilibili.BilibiliParser({})._parse_article_payload(payload)

    assert result.title == "传统专栏标题"
    assert result.author == "专栏作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("image", "https://i0.hdslb.com/article-cover.jpg"),
        ("image", "https://i0.hdslb.com/article-cover.jpg"),
        ("text", "完整正文第一段"),
        ("image", "https://i0.hdslb.com/content.jpg"),
        ("text", "完整正文第二段"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page_url", "api_path", "payload", "expected_referer", "expected_id"),
    [
        (
            "https://t.bilibili.com/123",
            "/x/polymer/web-dynamic/v1/detail",
            {
                "code": 0,
                "data": {
                    "item": {
                        "modules": {
                            "module_author": {"name": "动态作者"},
                            "module_dynamic": {
                                "major": {
                                    "type": "MAJOR_TYPE_OPUS",
                                    "opus": {
                                        "pics": [
                                            {
                                                "url": "https://i0.hdslb.com/dynamic.jpg@672w.webp"
                                            }
                                        ]
                                    },
                                }
                            },
                        }
                    }
                },
            },
            "https://www.bilibili.com",
            "123",
        ),
        (
            "https://www.bilibili.com/opus/456",
            "/x/polymer/web-dynamic/v1/opus/detail",
            {
                "code": 0,
                "data": {
                    "item": {
                        "basic": {"title": "图文标题"},
                        "modules": [
                            {
                                "module_content": {
                                    "paragraphs": [
                                        {
                                            "pic": {
                                                "pics": [
                                                    {
                                                        "url": "https://i0.hdslb.com/opus.jpg@!web-comment-note.avif"
                                                    }
                                                ]
                                            }
                                        }
                                    ]
                                }
                            }
                        ],
                    }
                },
            },
            "https://www.bilibili.com/opus/456",
            "456",
        ),
    ],
)
async def test_dynamic_and_opus_materialize_original_images(
    monkeypatch,
    page_url,
    api_path,
    payload,
    expected_referer,
    expected_id,
    assert_temporary_image,
):
    api_request = None
    image_request = None
    client_kwargs = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_request, image_request
        if request.url.path == api_path:
            api_request = request
            return httpx.Response(200, json=payload, request=request)
        image_request = request
        return httpx.Response(200, content=b"graphic-image", request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        nonlocal client_kwargs
        client_kwargs = kwargs
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bilibili.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser(
        {"bilibili_cookies": "SESSDATA=graphic-session"}
    ).parse(ParseContext(text=page_url))

    assert [item.kind for item in result.ordered_contents] == ["image"]
    assert_temporary_image(result, result.ordered_contents[0].value, b"graphic-image")
    assert image_request is not None
    assert str(image_request.url) in {
        "https://i0.hdslb.com/dynamic.jpg",
        "https://i0.hdslb.com/opus.jpg",
    }
    assert api_request is not None
    assert api_request.url.params["id"] == expected_id
    assert "opus_id" not in api_request.url.params
    assert "SESSDATA=graphic-session" in api_request.headers["Cookie"]
    assert "Cookie" not in image_request.headers
    assert image_request.headers["Referer"] == expected_referer
    assert client_kwargs is not None
    assert client_kwargs["headers"]["Referer"] == expected_referer
    assert "Mozilla/5.0" in client_kwargs["headers"]["User-Agent"]
    assert [cookie.domain for cookie in client_kwargs["cookies"].jar] == [
        ".bilibili.com"
    ]


@pytest.mark.asyncio
async def test_opus_falls_back_to_dynamic_article(monkeypatch):
    api_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        api_requests.append(request)
        if request.url.path == "/x/polymer/web-dynamic/v1/opus/detail":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"item": None, "fallback": True}},
                request=request,
            )
        if request.url.path == "/x/polymer/web-dynamic/v1/detail":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "item": {
                            "modules": {
                                "module_author": {"name": "专栏作者"},
                                "module_dynamic": {
                                    "desc": None,
                                    "major": {
                                        "type": "MAJOR_TYPE_ARTICLE",
                                        "article": {
                                            "id": 154325,
                                            "title": "传统专栏标题",
                                            "desc": "传统专栏摘要",
                                            "covers": [],
                                        },
                                    },
                                },
                            }
                        }
                    },
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "title": "传统专栏标题",
                    "author": {"name": "专栏作者"},
                    "content": "<p>完整专栏正文</p>",
                },
            },
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bilibili.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser({}).parse(
        ParseContext(text="https://www.bilibili.com/opus/73309181869226939")
    )

    assert result.title == "传统专栏标题"
    assert result.author == "专栏作者"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "完整专栏正文")
    ]
    assert [request.url.path for request in api_requests] == [
        "/x/polymer/web-dynamic/v1/opus/detail",
        "/x/polymer/web-dynamic/v1/detail",
        "/x/article/view",
    ]
    assert [request.url.params["id"] for request in api_requests] == [
        "73309181869226939",
        "73309181869226939",
        "154325",
    ]


@pytest.mark.asyncio
async def test_article_materializes_original_image_and_preserves_failed_slot(
    monkeypatch, assert_temporary_image
):
    article_url = "https://www.bilibili.com/read/cv789"
    payload = {
        "code": 0,
        "data": {
            "title": "专栏标题",
            "author": {"name": "专栏作者"},
            "origin_image_urls": [
                "//i0.hdslb.com/article-cover.jpg@672w.webp"
            ],
            "content": (
                "<p>第一段</p>"
                '<img src="//i0.hdslb.com/failed.jpg@!web-article-pic.avif">'
                "<p>第二段</p>"
                '<img src="//i0.hdslb.com/working.jpg@672w.webp">'
            ),
        },
    }
    image_requests = []
    article_request = None
    client_kwargs = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal article_request
        if request.url.path == "/x/article/view":
            article_request = request
            return httpx.Response(200, json=payload, request=request)
        image_requests.append(request)
        if request.url.path.endswith("failed.jpg"):
            return httpx.Response(403, request=request)
        return httpx.Response(200, content=b"article-image", request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        nonlocal client_kwargs
        client_kwargs = kwargs
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bilibili.httpx, "AsyncClient", create_client)

    result = await bilibili.BilibiliParser(
        {"bilibili_cookies": "SESSDATA=article-session"}
    ).parse(ParseContext(text=article_url))

    assert result.ordered_contents[0].kind == "image"
    assert_temporary_image(
        result, result.ordered_contents[0].value, b"article-image"
    )
    assert [(item.kind, item.value) for item in result.ordered_contents[1:4]] == [
        ("text", "第一段"),
        ("image_error", "第 2 张图片获取失败：HTTP 403"),
        ("text", "第二段"),
    ]
    assert result.ordered_contents[4].kind == "image"
    assert_temporary_image(result, result.ordered_contents[4].value, b"article-image")
    assert [str(request.url) for request in image_requests] == [
        "https://i0.hdslb.com/article-cover.jpg",
        "https://i0.hdslb.com/failed.jpg",
        "https://i0.hdslb.com/working.jpg",
    ]
    assert article_request is not None
    assert article_request.url.params["id"] == "789"
    assert "SESSDATA=article-session" in article_request.headers["Cookie"]
    assert all("Cookie" not in request.headers for request in image_requests)
    assert all(request.headers["Referer"] == article_url for request in image_requests)
    assert client_kwargs is not None
    assert client_kwargs["headers"]["Referer"] == article_url
    assert "Mozilla/5.0" in client_kwargs["headers"]["User-Agent"]
    assert [cookie.domain for cookie in client_kwargs["cookies"].jar] == [
        ".bilibili.com"
    ]


@pytest.mark.asyncio
async def test_video_materializes_original_cover(monkeypatch, assert_temporary_image):
    parser = bilibili.BilibiliParser({"bilibili_cookies": "SESSDATA=video-session"})

    async def get_video_info(video_id):
        return {
            "title": "视频标题",
            "author": "视频作者",
            "desc": "视频简介",
            "cid": "1",
            "pic": "https://i0.hdslb.com/video.jpg@672w.webp",
        }

    async def get_play_url(cid, video_id):
        return "https://video.example/play.mp4"

    image_request = None
    client_kwargs = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal image_request
        image_request = request
        return httpx.Response(200, content=b"video-cover", request=request)

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        nonlocal client_kwargs
        client_kwargs = kwargs
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(parser, "_get_video_info", get_video_info)
    monkeypatch.setattr(parser, "_get_play_url", get_play_url)
    monkeypatch.setattr(bilibili.httpx, "AsyncClient", create_client)

    result = await parser.parse(ParseContext(text="BV1xx411c7mD"))

    assert_temporary_image(result, result.cover_urls[0], b"video-cover")
    assert result.video_url == "https://video.example/play.mp4"
    assert image_request is not None
    assert str(image_request.url) == "https://i0.hdslb.com/video.jpg"
    assert "Cookie" not in image_request.headers
    assert image_request.headers["Referer"] == "https://www.bilibili.com"
    assert client_kwargs is not None
    assert client_kwargs["headers"]["Referer"] == "https://www.bilibili.com"
    assert "Mozilla/5.0" in client_kwargs["headers"]["User-Agent"]
    assert [cookie.domain for cookie in client_kwargs["cookies"].jar] == [
        ".bilibili.com"
    ]


@pytest.mark.asyncio
async def test_video_api_requests_use_bilibili_cookie(monkeypatch):
    requests = []
    client_kwargs = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/x/web-interface/view":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "title": "视频标题",
                        "pic": "",
                        "owner": {"name": "视频作者"},
                        "desc": "",
                        "cid": 123,
                    },
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={"code": 0, "data": {"durl": [{"url": "https://video.example"}]}},
            request=request,
        )

    async_client = httpx.AsyncClient

    def create_client(**kwargs):
        client_kwargs.append(kwargs)
        return async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(bilibili.httpx, "AsyncClient", create_client)
    parser = bilibili.BilibiliParser(
        {"bilibili_cookies": "SESSDATA=api-session; bili_jct=csrf-token"}
    )

    info = await parser._get_video_info("BV1xx411c7mD")
    play_url = await parser._get_play_url(str(info["cid"]), "BV1xx411c7mD")

    assert play_url == "https://video.example"
    assert [request.url.path for request in requests] == [
        "/x/web-interface/view",
        "/x/player/playurl",
    ]
    assert all(
        "SESSDATA=api-session" in request.headers["Cookie"] for request in requests
    )
    assert all(
        "bili_jct=csrf-token" in request.headers["Cookie"] for request in requests
    )
    assert all(
        [cookie.domain for cookie in kwargs["cookies"].jar]
        == [".bilibili.com", ".bilibili.com"]
        for kwargs in client_kwargs
    )


@pytest.mark.asyncio
async def test_bilibili_rejects_external_image_without_request():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=b"unexpected", request=request)

    parser = bilibili.BilibiliParser({})
    result = ParseResult(
        platform="bilibili", image_urls=["https://img.example/external.jpg"]
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await parser.materialize_images(result, client, "https://www.bilibili.com")

    assert requested_urls == []
    assert result.image_urls == [""]
    assert result.image_errors == {0: "第 1 张图片获取失败：InvalidURL"}
