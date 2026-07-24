import json

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.platforms.redbook import parser as redbook


def test_redbook_security_page_reports_missing_cookie():
    parser = redbook.RedBookParser({})
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.xiaohongshu.com/404/security-check"),
    )

    with pytest.raises(CookieAccessError, match="可能需要配置 Cookies"):
        parser._raise_for_auth_page(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://xhslink.com/a1b2c3",
        "https://www.xiaohongshu.com/explore/note123?xsec_token=token",
        "https://www.xiaohongshu.com/discovery/item/note123?xsec_token=token",
    ],
)
async def test_matches_supported_redbook_urls(url):
    assert await redbook.RedBookParser({}).match(ParseContext(text=url))


@pytest.mark.asyncio
async def test_parse_short_link_keeps_scheme_and_stops_after_first_redirect(
    monkeypatch,
):
    short_url = "http://xhslink.com/o/8ReowhzV8oo"
    discovery_url = (
        "https://www.xiaohongshu.com/discovery/item/note123?xsec_token=token"
    )
    explore_url = "https://www.xiaohongshu.com/explore/note123?xsec_token=token"
    security_url = "https://www.xiaohongshu.com/404/security-check"
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "title": "第一跳中的笔记",
                        "imageList": [],
                    }
                }
            }
        }
    }
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "xhslink.com":
            return httpx.Response(
                302, headers={"Location": discovery_url}, request=request
            )
        if request.url.path == "/discovery/item/note123":
            return httpx.Response(
                302, headers={"Location": security_url}, request=request
            )
        if request.url.path == "/404/security-check":
            return httpx.Response(200, text="security page", request=request)
        html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
        return httpx.Response(200, text=html, request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        redbook.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await redbook.RedBookParser({}).parse(ParseContext(text=short_url))

    assert result.title == "第一跳中的笔记"
    assert requested_urls == [short_url, explore_url]


@pytest.mark.asyncio
async def test_parse_explore_uses_minimal_desktop_headers(monkeypatch):
    page_url = "https://www.xiaohongshu.com/explore/note123?xsec_token=token"
    discovery_url = (
        "https://www.xiaohongshu.com/discovery/item/note123?xsec_token=token"
    )
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "title": "桌面页面中的笔记",
                        "imageList": [],
                    }
                }
            }
        }
    }
    explore_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal explore_request
        if request.url.path.startswith("/explore/"):
            explore_request = request
            if "Windows NT" not in request.headers["User-Agent"]:
                return httpx.Response(
                    302, headers={"Location": discovery_url}, request=request
                )
            html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
            return httpx.Response(200, text=html, request=request)
        html = '<script>window.__INITIAL_STATE__={"noteData": {}}</script>'
        return httpx.Response(200, text=html, request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        redbook.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await redbook.RedBookParser({}).parse(ParseContext(text=page_url))

    assert result.title == "桌面页面中的笔记"
    assert explore_request is not None
    assert "Accept" not in explore_request.headers
    assert "Origin" not in explore_request.headers
    assert "X-Requested-With" not in explore_request.headers
    assert not any(name.startswith("sec-fetch-") for name in explore_request.headers)


def test_explore_prefers_h265_video():
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "video",
                        "title": "视频标题",
                        "desc": "视频简介",
                        "user": {"nickname": "作者"},
                        "imageList": [{"urlDefault": "https://img.example/cover.jpg"}],
                        "video": {
                            "media": {
                                "stream": {
                                    "h264": [
                                        {"masterUrl": "https://video.example/h264.mp4"}
                                    ],
                                    "h265": [
                                        {"masterUrl": "https://video.example/h265.mp4"}
                                    ],
                                }
                            }
                        },
                    }
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_explore_state(state, "note123")

    assert result.video_url == "https://video.example/h265.mp4"
    assert result.cover_urls == ["https://img.example/cover.jpg"]
    assert result.description == "视频简介"


def test_explore_keeps_image_order():
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "title": "图文标题",
                        "desc": "图文简介",
                        "user": {"nickname": "作者"},
                        "imageList": [
                            {
                                "fileId": "notes_pre_post/original-1",
                                "traceId": "trace-ignored",
                                "urlDefault": "https://img.example/thumb-1.jpg",
                            },
                            {
                                "traceId": "notes_pre_post/original-2",
                                "urlDefault": "https://img.example/thumb-2.jpg",
                            },
                        ],
                    }
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_explore_state(state, "note123")

    assert result.image_urls == [
        "https://sns-img-qc.xhscdn.com/notes_pre_post/original-1",
        "https://sns-img-qc.xhscdn.com/notes_pre_post/original-2",
    ]


def test_explore_removes_only_explicit_transform_suffix():
    transformed = "https://img.example/path/photo.jpg!nd_dft_webp?token=1#preview"
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "imageList": [
                            {
                                "urlDefault": (
                                    "https://user:secret@img.example/private.jpg"
                                ),
                                "url": transformed,
                            },
                            {"url": "https://backup.example/image.jpg!transform"},
                        ],
                    }
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_explore_state(state, "note123")

    assert result.image_urls == [
        "https://img.example/path/photo.jpg?token=1#preview",
        "https://backup.example/image.jpg",
    ]


def test_discovery_continues_from_unsafe_default_to_safe_url():
    state = {
        "noteData": {
            "data": {
                "noteData": {
                    "type": "normal",
                    "imageList": [
                        {
                            "urlDefault": ("http://[::1/private.jpg!transform"),
                            "url": (
                                "https://img.example/original.jpg!transform"
                                "?token=1#preview"
                            ),
                        },
                        {
                            "urlDefault": (
                                "https://img.example/preferred-original.jpg"
                            ),
                            "url": "https://img.example/lower-priority.jpg",
                        },
                    ],
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_discovery_state(state)

    assert result.image_urls == [
        "https://img.example/original.jpg?token=1#preview",
        "https://img.example/preferred-original.jpg",
    ]


def test_explore_leaves_malformed_url_unchanged():
    malformed_url = "http://[::1/photo.jpg!transform"
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "imageList": [{"urlDefault": malformed_url}],
                    }
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_explore_state(state, "note123")

    assert result.image_urls == [malformed_url]


def test_explore_image_id_cannot_replace_original_cdn_authority():
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "imageList": [
                            {"fileId": "//evil.example/image.jpg?token=1#preview"}
                        ],
                    }
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_explore_state(state, "note123")

    assert result.image_urls == [
        "https://sns-img-qc.xhscdn.com/evil.example/image.jpg%3Ftoken%3D1%23preview"
    ]


def test_discovery_uses_large_video_cover_and_converts_metadata():
    state = {
        "noteData": {
            "normalNotePreloadData": {
                "imagesList": [
                    {
                        "url": "https://img.example/small.jpg",
                        "urlSizeLarge": "https://img.example/large.jpg",
                    }
                ]
            },
            "data": {
                "noteData": {
                    "type": "video",
                    "title": "兜底标题",
                    "desc": "兜底简介",
                    "user": {"nickName": "兜底作者"},
                    "imageList": [{"url": "https://img.example/watermark.jpg"}],
                    "video": {
                        "media": {
                            "stream": {
                                "h264": [
                                    {"masterUrl": "https://video.example/video.mp4"}
                                ]
                            }
                        }
                    },
                }
            },
        }
    }

    result = redbook.RedBookParser({})._parse_discovery_state(state)

    assert result.title == "兜底标题"
    assert result.author == "兜底作者"
    assert result.video_url == "https://video.example/video.mp4"
    assert result.cover_urls == ["https://img.example/large.jpg"]


def test_discovery_uses_file_ids_for_unwatermarked_images():
    state = {
        "noteData": {
            "data": {
                "noteData": {
                    "type": "normal",
                    "title": "图文标题",
                    "desc": "图文简介",
                    "user": {"nickName": "作者"},
                    "imageList": [
                        {
                            "url": "https://img.example/watermark-1.jpg",
                            "fileId": "notes_pre_post/image-1",
                        },
                        {
                            "url": "https://img.example/watermark-2.jpg",
                            "fileId": "notes_pre_post/image-2",
                        },
                    ],
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_discovery_state(state)

    assert result.image_urls == [
        "https://sns-img-qc.xhscdn.com/notes_pre_post/image-1",
        "https://sns-img-qc.xhscdn.com/notes_pre_post/image-2",
    ]


@pytest.mark.parametrize(
    ("image_url", "expected_url"),
    [
        (
            "https://img.example/photo.jpg!webp?token=1#preview",
            "https://img.example/photo.jpg?token=1#preview",
        ),
        (
            "https://img.example.evil.test/photo.jpg!webp?token=1#preview",
            "https://img.example.evil.test/photo.jpg?token=1#preview",
        ),
        ("http://[::1/photo.jpg!webp", "http://[::1/photo.jpg!webp"),
    ],
)
def test_discovery_without_file_id_normalizes_url_safely(image_url, expected_url):
    state = {
        "noteData": {
            "data": {
                "noteData": {
                    "type": "normal",
                    "imageList": [{"url": image_url}],
                }
            }
        }
    }

    result = redbook.RedBookParser({})._parse_discovery_state(state)

    assert result.image_urls == [expected_url]


def test_discovery_video_cover_prefers_original_trace_id():
    state = {
        "noteData": {
            "normalNotePreloadData": {
                "imagesList": [
                    {
                        "traceId": "notes_pre_post/video-cover",
                        "urlSizeLarge": (
                            "https://cover.example/video.jpg!large?token=1#frame"
                        ),
                    }
                ]
            },
            "data": {
                "noteData": {
                    "type": "video",
                    "imageList": [],
                    "video": {
                        "media": {
                            "stream": {
                                "h264": [
                                    {"masterUrl": "https://video.example/video.mp4"}
                                ]
                            }
                        }
                    },
                }
            },
        }
    }

    result = redbook.RedBookParser({})._parse_discovery_state(state)

    assert result.cover_urls == [
        "https://sns-img-qc.xhscdn.com/notes_pre_post/video-cover"
    ]


@pytest.mark.asyncio
async def test_parse_explore_materializes_original_without_leaking_cookies(
    monkeypatch, assert_temporary_image
):
    page_url = "https://www.xiaohongshu.com/explore/note123?xsec_token=token"
    image_url = "https://sns-img-qc.xhscdn.com/notes_pre_post/original"
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "title": "图文标题",
                        "imageList": [
                            {
                                "fileId": "notes_pre_post/original",
                                "urlDefault": "https://img.example/thumb.jpg",
                            }
                        ],
                    }
                }
            }
        }
    }
    image_request = None
    page_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal image_request, page_request
        if request.url.host == "www.xiaohongshu.com":
            page_request = request
            html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
            return httpx.Response(200, text=html, request=request)
        image_request = request
        assert str(request.url) == image_url
        return httpx.Response(200, content=b"original-image", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        redbook.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await redbook.RedBookParser(
        {"redbook_cookies": "web_session=cookie-value"}
    ).parse(ParseContext(text=page_url))

    assert_temporary_image(result, result.image_urls[0], b"original-image")
    assert image_request is not None
    assert image_request.headers["Referer"] == page_url.split("?", 1)[0]
    assert "Mobile/15E148" in image_request.headers["User-Agent"]
    assert image_request.headers["Origin"] == "https://www.xiaohongshu.com"
    assert "Cookie" not in image_request.headers
    assert page_request is not None
    assert "web_session=cookie-value" in page_request.headers["Cookie"]


@pytest.mark.asyncio
async def test_parse_discovery_materializes_and_keeps_failed_slot(
    monkeypatch, assert_temporary_image
):
    source_url = "https://www.xiaohongshu.com/discovery/item/note123"
    discovery_url = source_url
    successful_url = "https://sns-img-qc.xhscdn.com/original-success"
    failed_url = "https://sns-img-qc.xhscdn.com/original-failed.jpg"
    state = {
        "noteData": {
            "data": {
                "noteData": {
                    "type": "normal",
                    "title": "兜底标题",
                    "imageList": [
                        {"fileId": "original-success"},
                        {"url": f"{failed_url}!webp"},
                    ],
                }
            }
        }
    }
    image_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/explore/"):
            return httpx.Response(200, text="no state", request=request)
        if request.url.host == "www.xiaohongshu.com":
            html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
            return httpx.Response(200, text=html, request=request)
        image_requests.append(request)
        if str(request.url) == failed_url:
            return httpx.Response(403, request=request)
        assert str(request.url) == successful_url
        return httpx.Response(200, content=b"discovery-image", request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers=redbook.RedBookParser.HEADERS,
        cookies={"web_session": "cookie-value"},
    )
    monkeypatch.setattr(redbook.httpx, "AsyncClient", lambda **kwargs: client)

    result = await redbook.RedBookParser({}).parse(ParseContext(text=source_url))

    assert_temporary_image(result, result.image_urls[0], b"discovery-image")
    assert result.image_urls[1] == ""
    assert result.image_errors == {1: "第 2 张图片获取失败：HTTP 403"}
    assert [request.headers["Referer"] for request in image_requests] == [
        discovery_url,
        discovery_url,
    ]


@pytest.mark.asyncio
async def test_parse_explore_materializes_video_cover_with_session(
    monkeypatch, assert_temporary_image
):
    page_url = "https://www.xiaohongshu.com/explore/video123?xsec_token=token"
    cover_url = "https://sns-img-qc.xhscdn.com/notes_pre_post/explore-cover"
    video_url = "https://video.example/explore.mp4"
    state = {
        "note": {
            "noteDetailMap": {
                "video123": {
                    "note": {
                        "type": "video",
                        "title": "视频标题",
                        "imageList": [{"fileId": "notes_pre_post/explore-cover"}],
                        "video": {
                            "media": {"stream": {"h265": [{"masterUrl": video_url}]}}
                        },
                    }
                }
            }
        }
    }
    cover_request = None
    page_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cover_request, page_request
        if request.url.host == "www.xiaohongshu.com":
            page_request = request
            html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
            return httpx.Response(200, text=html, request=request)
        cover_request = request
        assert str(request.url) == cover_url
        return httpx.Response(200, content=b"explore-cover", request=request)

    constructor_kwargs = {}
    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        constructor_kwargs.update(kwargs)
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(redbook.httpx, "AsyncClient", client_factory)

    result = await redbook.RedBookParser(
        {"redbook_cookies": "web_session=explore-session"}
    ).parse(ParseContext(text=page_url))

    assert_temporary_image(result, result.cover_urls[0], b"explore-cover")
    assert result.video_url == video_url
    assert cover_request is not None
    assert cover_request.headers["Referer"] == page_url.split("?", 1)[0]
    assert "Cookie" not in cover_request.headers
    assert cover_request.headers["Origin"] == "https://www.xiaohongshu.com"
    assert page_request is not None
    assert str(page_request.url) == page_url
    assert "web_session=explore-session" in page_request.headers["Cookie"]
    assert constructor_kwargs["headers"] == redbook.RedBookParser.HEADERS
    assert [cookie.domain for cookie in constructor_kwargs["cookies"].jar] == [
        ".xiaohongshu.com"
    ]


@pytest.mark.asyncio
async def test_parse_discovery_materializes_video_cover_with_session(
    monkeypatch, assert_temporary_image
):
    source_url = "https://www.xiaohongshu.com/discovery/item/video123"
    cover_url = "https://sns-img-qc.xhscdn.com/notes_pre_post/discovery-cover"
    video_url = "https://video.example/discovery.mp4"
    state = {
        "noteData": {
            "normalNotePreloadData": {
                "imagesList": [{"traceId": "notes_pre_post/discovery-cover"}]
            },
            "data": {
                "noteData": {
                    "type": "video",
                    "imageList": [],
                    "video": {
                        "media": {"stream": {"h264": [{"masterUrl": video_url}]}}
                    },
                }
            },
        }
    }
    cover_request = None
    page_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal cover_request
        if request.url.path.startswith("/explore/"):
            page_requests.append(request)
            return httpx.Response(200, text="no state", request=request)
        if request.url.host == "www.xiaohongshu.com":
            page_requests.append(request)
            html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
            return httpx.Response(200, text=html, request=request)
        cover_request = request
        assert str(request.url) == cover_url
        return httpx.Response(200, content=b"discovery-cover", request=request)

    constructor_kwargs = {}
    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        constructor_kwargs.update(kwargs)
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(redbook.httpx, "AsyncClient", client_factory)

    result = await redbook.RedBookParser(
        {"redbook_cookies": "web_session=discovery-session"}
    ).parse(ParseContext(text=source_url))

    assert_temporary_image(result, result.cover_urls[0], b"discovery-cover")
    assert result.video_url == video_url
    assert cover_request is not None
    assert cover_request.headers["Referer"] == source_url
    assert "Cookie" not in cover_request.headers
    assert cover_request.headers["Origin"] == "https://www.xiaohongshu.com"
    assert len(page_requests) == 2
    assert all(
        "web_session=discovery-session" in request.headers["Cookie"]
        for request in page_requests
    )
    assert constructor_kwargs["headers"] == redbook.RedBookParser.HEADERS
    assert [cookie.domain for cookie in constructor_kwargs["cookies"].jar] == [
        ".xiaohongshu.com"
    ]


@pytest.mark.asyncio
async def test_parse_keeps_unsafe_redbook_candidates_without_requesting_them(
    monkeypatch,
):
    page_url = "https://www.xiaohongshu.com/explore/note123?xsec_token=secret"
    unsafe_urls = [
        "https://user:secret@img.example/private.webp",
        "https://img.example:8080/private.webp",
        "ftp://img.example/private.webp",
    ]
    state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "normal",
                        "imageList": [{"urlDefault": url} for url in unsafe_urls],
                    }
                }
            }
        }
    }
    unexpected_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.xiaohongshu.com":
            html = f"<script>window.__INITIAL_STATE__={json.dumps(state)}</script>"
            return httpx.Response(200, text=html, request=request)
        unexpected_requests.append(request)
        return httpx.Response(200, content=b"must-not-download", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        redbook.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await redbook.RedBookParser({}).parse(ParseContext(text=page_url))

    assert unexpected_requests == []
    assert result.image_urls == ["", "", ""]
    assert result.image_count == 3
    assert list(result.image_errors) == [0, 1, 2]


def test_redbook_payloads_handle_null_strings_and_mixed_lists_safely():
    parser = redbook.RedBookParser({})
    with pytest.raises(ValueError, match="Explore 页面中未找到笔记数据"):
        parser._parse_explore_state({"note": {"noteDetailMap": "invalid"}}, "note123")
    with pytest.raises(ValueError, match="Discovery 页面中未找到笔记数据"):
        parser._parse_discovery_state({"noteData": "invalid"})

    explore_state = {
        "note": {
            "noteDetailMap": {
                "note123": {
                    "note": {
                        "type": "video",
                        "user": "invalid-user",
                        "imageList": [
                            None,
                            "invalid-image",
                            {"urlDefault": "https://img.example/cover.jpg"},
                        ],
                        "video": {
                            "media": {
                                "stream": {
                                    "h265": [None, "invalid-variant"],
                                    "h264": [
                                        None,
                                        {
                                            "masterUrl": "https://video.example/video.mp4"
                                        },
                                    ],
                                }
                            }
                        },
                    }
                }
            }
        }
    }

    result = parser._parse_explore_state(explore_state, "note123")

    assert result.author == "未知作者"
    assert result.cover_urls == ["https://img.example/cover.jpg"]
    assert result.video_url == "https://video.example/video.mp4"


def test_extract_initial_state_replaces_javascript_undefined():
    payload = {"note": {"value": None}}
    html = (
        "<script>window.__INITIAL_STATE__="
        + json.dumps(payload).replace("null", "undefined")
        + "</script>"
    )

    assert redbook.RedBookParser({})._extract_initial_state(html) == payload
