import json

import httpx
import pytest
from astrbot_multi_parser.core.http import CookieAccessError
from astrbot_multi_parser.models import ParseContext
from astrbot_multi_parser.platforms import douyin
from astrbot_multi_parser.platforms.douyin import music as douyin_music
from astrbot_multi_parser.platforms.douyin import parser as douyin_parser


def test_douyin_login_redirect_reports_stale_cookie_without_leak():
    parser = douyin.DouyinParser({"douyin_cookies": "sessionid=secret"})
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://www.douyin.com/passport/general/login"),
    )

    with pytest.raises(CookieAccessError, match="Cookies 可能已失效") as exc_info:
        parser._raise_for_auth_page(response)

    assert "secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_probe_video_url_does_not_read_body_when_range_is_ignored():
    class FailingVideoStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("视频探测不得读取响应正文")
            yield b""  # pragma: no cover

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(500 * 1024 * 1024)},
            stream=FailingVideoStream(),
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        url = await douyin.DouyinParser({})._probe_video_url(
            client, "video-token", "https://www.iesdouyin.com/share/video/1/"
        )

    assert url == ""


@pytest.mark.asyncio
async def test_matches_only_mainland_douyin_urls():
    parser = douyin.DouyinParser({})

    assert await parser.match(ParseContext(text="https://v.douyin.com/abc123"))
    assert await parser.match(
        ParseContext(text="https://live.douyin.com/123456789?room_id=987654321")
    )
    assert await parser.match(
        ParseContext(
            text=(
                "https://webcast.amemv.com/douyin/webcast/reflow/"
                "7666067452786608950"
            )
        )
    )
    assert await parser.match(
        ParseContext(text="https://music.douyin.com/qishui/share/track?track_id=123456")
    )
    assert await parser.match(
        ParseContext(text="https://www.douyin.com/video/7521023890996514083")
    )
    assert not await parser.match(ParseContext(text="https://vm.tiktok.com/abc123"))
    assert not await parser.match(
        ParseContext(text="https://www.tiktok.com/@user/video/123")
    )


def test_live_payload_extracts_room_information():
    payload = {
        "status_code": 0,
        "data": {
            "data": [
                {
                    "id_str": "987654321",
                    "status": 2,
                    "title": "抖音直播标题",
                    "user": {"nickname": "抖音主播"},
                    "cover": {
                        "url_list": [
                            "https://p3-webcast.douyinpic.com/live-cover.webp"
                        ]
                    },
                    "room_view_stats": {"display_value": "1.2万"},
                }
            ]
        },
    }

    result = douyin.DouyinParser({})._parse_live_data(payload)

    assert result.title == "抖音直播标题"
    assert result.author == "抖音主播"
    assert result.cover_urls == [
        "https://p3-webcast.douyinpic.com/live-cover.webp"
    ]
    assert result.extra_lines == [
        "直播状态: 直播中",
        "观看人数: 1.2万",
    ]


@pytest.mark.asyncio
async def test_parse_live_requests_api_and_materializes_cover(
    monkeypatch, assert_temporary_image
):
    live_url = "https://live.douyin.com/123456789"
    cover_url = "https://p3-webcast.douyinpic.com/live-cover.webp"
    api_request = None
    image_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_request, image_request
        if request.url.path == "/webcast/room/web/enter/":
            api_request = request
            return httpx.Response(
                200,
                json={
                    "status_code": 0,
                    "data": {
                        "data": [
                            {
                                "status": 2,
                                "title": "抖音直播标题",
                                "user": {"nickname": "抖音主播"},
                                "cover": {"url_list": [cover_url]},
                            }
                        ]
                    },
                },
                request=request,
            )
        image_request = request
        return httpx.Response(200, content=b"live-cover", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        douyin_parser.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await douyin.DouyinParser(
        {"douyin_cookies": "ttwid=live-session"}
    ).parse(ParseContext(text=live_url))

    assert result.title == "抖音直播标题"
    assert_temporary_image(result, result.cover_urls[0], b"live-cover")
    assert api_request is not None
    assert api_request.url.params["web_rid"] == "123456789"
    assert "ttwid=live-session" in api_request.headers["Cookie"]
    assert image_request is not None
    assert image_request.headers["Referer"] == live_url
    assert "Cookie" not in image_request.headers


@pytest.mark.asyncio
async def test_short_link_redirects_to_live_reflow_page(
    monkeypatch, assert_temporary_image
):
    short_url = "https://v.douyin.com/0-I94aXdAUs/"
    room_url = (
        "https://webcast.amemv.com/douyin/webcast/reflow/7666067452786608950"
    )
    cover_url = "https://p3-webcast.douyinpic.com/reflow-cover.webp"
    page_request = None
    image_request = None
    room_data = {
        "idStr": "7666067452786608950",
        "status": 2,
        "title": "回流页直播标题",
        "owner": {"nickname": "回流页主播"},
        "cover": {"urlList": [cover_url]},
        "userCount": 321,
    }
    rsc_data = json.dumps(
        ["$", "$L7", None, {"data": {"room": room_data}}],
        ensure_ascii=False,
    )
    rsc_payload = json.dumps([1, f"5:{rsc_data}"], ensure_ascii=False)
    html = f"<script>self.__rsc_f.push({rsc_payload})</script>"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_request, image_request
        if request.url.host == "v.douyin.com":
            return httpx.Response(
                302, headers={"Location": room_url}, request=request
            )
        if request.url.host == "webcast.amemv.com":
            page_request = request
            return httpx.Response(200, text=html, request=request)
        image_request = request
        return httpx.Response(200, content=b"reflow-cover", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        douyin_parser.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await douyin.DouyinParser({}).parse(ParseContext(text=short_url))

    assert result.title == "回流页直播标题"
    assert result.author == "回流页主播"
    assert result.extra_lines == [
        "直播状态: 直播中",
        "观看人数: 321",
    ]
    assert_temporary_image(result, result.cover_urls[0], b"reflow-cover")
    assert page_request is not None
    assert image_request is not None
    assert image_request.headers["Referer"] == room_url
    assert "Cookie" not in image_request.headers


def test_qishui_track_html_parses_summary_cover_and_audio():
    html = """
    <html>
      <head>
        <meta name="description" content="歌曲简介">
      </head>
      <body>
        <h1 class="title">苏北的北</h1>
        <span class="artist-name-max">小阿娇</span>
        <img alt="a-image" src="https://p3-luna.douyinpic.com/cover.jpg">
        <audio id="--luna-view-player--"
               src="https://v3-luna.douyinvod.com/song.m4a?a=1&amp;b=2"></audio>
      </body>
    </html>
    """

    result = douyin_music.parse_qishui_track_html(html, platform="douyin")

    assert result.title == "苏北的北"
    assert result.author == "小阿娇"
    assert result.description == "歌曲简介"
    assert result.cover_urls == ["https://p3-luna.douyinpic.com/cover.jpg"]
    assert result.audio_url == ("https://v3-luna.douyinvod.com/song.m4a?a=1&b=2")
    assert result.extra_lines == []


def test_qishui_track_html_rejects_untrusted_audio_url():
    html = """
    <h1 class="title">歌曲</h1>
    <audio id="--luna-view-player--"
           src="https://user:secret@example.com/song.m4a"></audio>
    """

    result = douyin_music.parse_qishui_track_html(html, platform="douyin")

    assert result.audio_url == ""
    assert result.extra_lines == ["无法获取安全的音频直链。"]


def test_qishui_track_html_parses_audio_from_router_data():
    html = r"""
    <h1 class="title">歌曲</h1>
    <script async data-script-src="modern-inline">
    _ROUTER_DATA = {"loaderData":{"track_page":{"audioWithLyricsOption":{
      "url":"https:\u002F\u002Fv5-se-ex-mc-luna.douyinvod.com\u002Fsong.m4a"
    }}}}
    ;window.__INITIALIZED__ = true;
    </script>
    """

    result = douyin_music.parse_qishui_track_html(html, platform="douyin")

    assert result.audio_url == ("https://v5-se-ex-mc-luna.douyinvod.com/song.m4a")


@pytest.mark.asyncio
async def test_short_link_redirects_to_qishui_track(
    monkeypatch, assert_temporary_image
):
    short_url = "https://v.douyin.com/XwBGrQNYEYE/"
    music_url = "https://music.douyin.com/qishui/share/track?track_id=123456"
    cover_url = "https://p3-luna.douyinpic.com/cover.jpg"
    audio_url = "https://v3-luna.douyinvod.com/song.m4a"
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "v.douyin.com":
            return httpx.Response(
                302,
                headers={"Location": music_url},
                request=request,
            )
        if request.url.host == "music.douyin.com":
            html = f"""
            <meta name="description" content="歌曲简介">
            <h1 class="title">歌曲标题</h1>
            <span class="artist-name-max">歌手</span>
            <img alt="a-image" src="{cover_url}">
            <script data-script-src="modern-inline">
            _ROUTER_DATA = {{"loaderData":{{"track_page":{{
              "audioWithLyricsOption":{{"url":"{audio_url}"}}
            }}}}}}
            </script>
            """
            return httpx.Response(200, text=html, request=request)
        if request.url.host == "p3-luna.douyinpic.com":
            return httpx.Response(200, content=b"cover-image", request=request)
        raise AssertionError(f"发生未预期请求: {request.url.host}")

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        douyin_parser.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await douyin.DouyinParser({}).parse(ParseContext(text=short_url))

    assert result.title == "歌曲标题"
    assert result.author == "歌手"
    assert result.description == "歌曲简介"
    assert result.audio_url == audio_url
    assert_temporary_image(result, result.cover_urls[0], b"cover-image")
    assert requested_hosts == [
        "v.douyin.com",
        "music.douyin.com",
        "p3-luna.douyinpic.com",
    ]


def test_router_data_parses_image_note():
    payload = {
        "loaderData": {
            "note_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "desc": "图文标题",
                            "create_time": 123,
                            "author": {"nickname": "作者"},
                            "images": [
                                {
                                    "url_list": [
                                        "",
                                        "https://img.example/original-1.webp",
                                    ],
                                    "download_url_list": [
                                        "https://img.example/original-1-water:1080:1440.webp"
                                    ],
                                },
                                {
                                    "url_list": [""],
                                    "download_url_list": [
                                        "",
                                        "https://img.example/fallback-2.webp",
                                    ],
                                },
                            ],
                        }
                    ]
                }
            }
        }
    }

    result = douyin.DouyinParser({})._parse_router_data(payload)

    assert result.title == "图文标题"
    assert result.author == "作者"
    assert result.image_urls == [
        "https://img.example/original-1.webp",
        "https://img.example/fallback-2.webp",
    ]
    assert result.video_url == ""


def test_router_data_falls_back_to_unwatermarked_play_addr():
    payload = {
        "loaderData": {
            "video_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "desc": "视频标题",
                            "author": {"nickname": "作者"},
                            "video": {
                                "play_addr": {
                                    "uri": "video-token",
                                    "url_list": ["https://video.example/playwm?id=1"],
                                },
                                "cover": {
                                    "url_list": ["https://img.example/cover.jpg"]
                                },
                            },
                        }
                    ]
                }
            }
        }
    }

    result = douyin.DouyinParser({})._parse_router_data(payload)

    assert result.video_url == "https://video.example/play?id=1"
    assert result.cover_urls == ["https://img.example/cover.jpg"]
    assert result.extra_lines == ["play_token=video-token"]


def test_slides_data_keeps_static_image_order():
    payload = {
        "aweme_details": [
            {
                "desc": "Slides 标题",
                "author": {"nickname": "作者"},
                "images": [
                    {
                        "url_list": [
                            "",
                            "https://img.example/original-1.webp",
                        ],
                        "download_url_list": [
                            "https://img.example/original-1-water:1080:1440.webp"
                        ],
                        "video": {
                            "play_addr": {
                                "url_list": ["https://video.example/effect.mp4"]
                            }
                        },
                    },
                    {
                        "url_list": [],
                        "download_url_list": ["https://img.example/fallback-2.webp"],
                    },
                ],
            }
        ]
    }

    result = douyin.DouyinParser({})._parse_slides_data(payload)

    assert result.image_urls == [
        "https://img.example/original-1.webp",
        "https://img.example/fallback-2.webp",
    ]
    assert result.video_url == ""


@pytest.mark.asyncio
async def test_video_probe_selects_largest_candidate():
    sizes = {"1080p": 1000, "720p": 2000, "540p": 700, "360p": 300}

    def handler(request: httpx.Request) -> httpx.Response:
        ratio = request.url.params["ratio"]
        return httpx.Response(
            206,
            headers={"Content-Range": f"bytes 0-1/{sizes[ratio]}"},
            request=request,
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        url = await douyin.DouyinParser({})._probe_video_url(
            client, "video-token", "https://www.iesdouyin.com/share/video/1/"
        )

    assert url.endswith("ratio=720p")


@pytest.mark.asyncio
async def test_parse_materializes_images_without_leaking_douyin_cookies(
    monkeypatch, assert_temporary_image
):
    image_url = "https://p3-sign.douyinpic.com/image.webp?token=secret"
    share_url = "https://www.iesdouyin.com/share/note/123/"
    router_data = {
        "loaderData": {
            "note_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "desc": "图文标题",
                            "author": {"nickname": "作者"},
                            "images": [
                                {
                                    "url_list": [image_url],
                                    "download_url_list": [
                                        "https://p3-sign.douyinpic.com/image-water:1080:1440.webp"
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        }
    }
    image_request = None
    page_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal image_request, page_request
        if request.url.host == "www.iesdouyin.com":
            page_request = request
            html = f"<script>window._ROUTER_DATA = {router_data!r}</script>".replace(
                "'", '"'
            )
            return httpx.Response(200, text=html, request=request)
        image_request = request
        return httpx.Response(200, content=b"image-content", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        douyin_parser.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await douyin.DouyinParser({"douyin_cookies": "ttwid=test-session"}).parse(
        ParseContext(text=share_url)
    )

    assert_temporary_image(result, result.image_urls[0], b"image-content")
    assert image_request is not None
    assert image_request.headers["Referer"] == share_url
    assert "Mobile/15E148" in image_request.headers["User-Agent"]
    assert "Cookie" not in image_request.headers
    assert page_request is not None
    assert "ttwid=test-session" in page_request.headers["Cookie"]


@pytest.mark.asyncio
async def test_parse_keeps_failed_douyin_image_slot(
    monkeypatch, caplog, assert_temporary_image
):
    failed_url = "https://p3-sign.douyinpic.com/failed.webp?token=secret"
    working_url = "https://p26-sign.douyinpic.com/working.webp?token=secret"
    share_url = "https://www.iesdouyin.com/share/note/123/"
    router_data = {
        "loaderData": {
            "note_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "desc": "图文标题",
                            "author": {"nickname": "作者"},
                            "images": [
                                {"url_list": [failed_url]},
                                {"url_list": [working_url]},
                            ],
                        }
                    ]
                }
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.iesdouyin.com":
            html = f"<script>window._ROUTER_DATA = {router_data!r}</script>".replace(
                "'", '"'
            )
            return httpx.Response(200, text=html, request=request)
        if request.url.host == "p3-sign.douyinpic.com":
            return httpx.Response(403, request=request)
        return httpx.Response(200, content=b"working-image", request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers=douyin.DouyinParser.IOS_HEADERS,
        cookies={"ttwid": "test-session"},
    )
    monkeypatch.setattr(douyin_parser.httpx, "AsyncClient", lambda **kwargs: client)

    result = await douyin.DouyinParser({}).parse(ParseContext(text=share_url))

    assert result.title == "图文标题"
    assert result.image_urls[0] == ""
    assert_temporary_image(result, result.image_urls[1], b"working-image")
    assert result.image_errors == {0: "第 1 张图片获取失败：HTTP 403"}
    warning = next(
        record.message
        for record in caplog.records
        if record.message.startswith("图片下载失败")
    )
    assert "p3-sign.douyinpic.com" in warning
    assert "token=secret" not in warning


@pytest.mark.asyncio
async def test_parse_materializes_video_cover(monkeypatch, assert_temporary_image):
    cover_url = "https://p3-sign.douyinpic.com/original-cover.webp"
    share_url = "https://www.iesdouyin.com/share/video/123/"
    router_data = {
        "loaderData": {
            "video_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "desc": "视频标题",
                            "author": {"nickname": "作者"},
                            "video": {
                                "play_addr": {
                                    "url_list": ["https://video.example/play.mp4"]
                                },
                                "cover": {"url_list": [cover_url]},
                            },
                        }
                    ]
                }
            }
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.iesdouyin.com":
            html = f"<script>window._ROUTER_DATA = {router_data!r}</script>".replace(
                "'", '"'
            )
            return httpx.Response(200, text=html, request=request)
        return httpx.Response(200, content=b"original-cover", request=request)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        headers=douyin.DouyinParser.IOS_HEADERS,
        cookies={"ttwid": "test-session"},
    )
    monkeypatch.setattr(douyin_parser.httpx, "AsyncClient", lambda **kwargs: client)

    result = await douyin.DouyinParser({}).parse(ParseContext(text=share_url))

    assert_temporary_image(result, result.cover_urls[0], b"original-cover")


@pytest.mark.asyncio
async def test_parse_slides_materializes_original_candidates_in_place(
    monkeypatch, assert_temporary_image
):
    share_url = "https://www.iesdouyin.com/share/slides/123/"
    original_url = "https://p3-sign.douyinpic.com/original-1.webp"
    fallback_url = "https://p26-sign.douyinpic.com/fallback-2.webp"
    failed_url = "https://p9-sign.douyinpic.com/original-failed.webp"
    slides_data = {
        "aweme_details": [
            {
                "desc": "Slides 标题",
                "author": {"nickname": "作者"},
                "images": [
                    {
                        "url_list": ["", original_url],
                        "download_url_list": [
                            "https://p3-sign.douyinpic.com/original-1-water:1080:1440.webp"
                        ],
                    },
                    {
                        "url_list": [""],
                        "download_url_list": ["", fallback_url],
                    },
                    {
                        "url_list": [failed_url],
                        "download_url_list": [
                            "https://p9-sign.douyinpic.com/original-failed-water:1080:1440.webp"
                        ],
                    },
                ],
            }
        ]
    }
    image_requests = []
    slides_request = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal slides_request
        if request.url.path == "/web/api/v2/aweme/slidesinfo/":
            slides_request = request
            assert request.url.params["aweme_ids"] == "[123]"
            return httpx.Response(200, json=slides_data, request=request)
        image_requests.append(request)
        if str(request.url) == failed_url:
            return httpx.Response(403, request=request)
        return httpx.Response(200, content=str(request.url).encode(), request=request)

    constructor_kwargs = {}
    real_async_client = httpx.AsyncClient

    def client_factory(**kwargs):
        constructor_kwargs.update(kwargs)
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(douyin_parser.httpx, "AsyncClient", client_factory)

    result = await douyin.DouyinParser(
        {"douyin_cookies": "sessionid=slides-session"}
    ).parse(ParseContext(text=share_url))

    assert_temporary_image(result, result.image_urls[0], original_url.encode())
    assert_temporary_image(result, result.image_urls[1], fallback_url.encode())
    assert result.image_urls[2] == ""
    assert result.image_errors == {2: "第 3 张图片获取失败：HTTP 403"}
    assert [str(request.url) for request in image_requests] == [
        original_url,
        fallback_url,
        failed_url,
    ]
    assert all(request.headers["Referer"] == share_url for request in image_requests)
    assert all("Cookie" not in request.headers for request in image_requests)
    assert slides_request is not None
    assert "sessionid=slides-session" in slides_request.headers["Cookie"]
    assert constructor_kwargs["headers"] == douyin.DouyinParser.IOS_HEADERS
    assert sorted(cookie.domain for cookie in constructor_kwargs["cookies"].jar) == [
        ".douyin.com",
        ".iesdouyin.com",
    ]


@pytest.mark.asyncio
async def test_parse_keeps_unsafe_douyin_candidates_without_requesting_them(
    monkeypatch,
):
    share_url = "https://www.iesdouyin.com/share/slides/123/"
    unsafe_urls = [
        "https://user:secret@img.example/private.webp",
        "https://img.example:8080/private.webp",
        "ftp://img.example/private.webp",
    ]
    slides_data = {
        "aweme_details": [
            {"images": [{"download_url_list": [url]} for url in unsafe_urls]}
        ]
    }
    unexpected_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/web/api/v2/aweme/slidesinfo/":
            return httpx.Response(200, json=slides_data, request=request)
        unexpected_requests.append(request)
        return httpx.Response(200, content=b"must-not-download", request=request)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        douyin_parser.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )

    result = await douyin.DouyinParser({}).parse(ParseContext(text=share_url))

    assert unexpected_requests == []
    assert result.image_urls == ["", "", ""]
    assert result.image_count == 3
    assert list(result.image_errors) == [0, 1, 2]


def test_douyin_image_payloads_ignore_string_containers_and_mixed_elements():
    router_payload = {
        "loaderData": {
            "note_(id)/page": {
                "videoInfoRes": {
                    "item_list": [
                        {
                            "author": "invalid-author",
                            "images": [
                                None,
                                "invalid-image",
                                {
                                    "download_url_list": "https://chars.example",
                                    "url_list": [None, "", "https://img.example/a.jpg"],
                                },
                            ],
                        }
                    ]
                }
            }
        }
    }
    slides_payload = {
        "aweme_details": [
            {
                "author": None,
                "images": [
                    "invalid-image",
                    {"url_list": "https://chars.example"},
                    {"url_list": [None, "https://img.example/b.jpg"]},
                ],
            }
        ]
    }

    router_result = douyin.DouyinParser({})._parse_router_data(router_payload)
    slides_result = douyin.DouyinParser({})._parse_slides_data(slides_payload)

    assert router_result.image_urls == ["https://img.example/a.jpg"]
    assert router_result.author == "未知作者"
    assert slides_result.image_urls == ["https://img.example/b.jpg"]
    assert slides_result.author == "未知作者"


def test_douyin_image_candidates_prefer_unwatermarked_url_list():
    image = {
        "url_list": [
            "https://p3-sign.douyinpic.com/source~tplv-dy-lqen-new:1080:1440:q80.webp"
        ],
        "download_url_list": [
            "https://p3-sign.douyinpic.com/source~tplv-dy-lqen-new-water:1080:1440:user:q80.webp"
        ],
    }

    selected = douyin.DouyinParser._select_image_url(image)

    assert selected == image["url_list"][0]


def test_douyin_image_candidates_reject_watermarked_only_urls():
    image = {
        "url_list": [],
        "download_url_list": [
            "https://p3-sign.douyinpic.com/source~tplv-dy-lqen-new-water:1080:1440:user:q80.webp"
        ],
    }

    selected = douyin.DouyinParser._select_image_url(image)

    assert selected == douyin.DouyinParser.INVALID_IMAGE_URL


def test_douyin_image_candidates_continue_after_unsafe_values():
    payload = {
        "aweme_details": [
            {
                "images": [
                    {
                        "url_list": [
                            "https://user:secret@img.example/private.webp",
                            "http://[::1/private.webp",
                            "https://img.example/source-water:1080:1440.webp",
                        ],
                        "download_url_list": [
                            "https://img.example/original-after-unsafe.webp"
                        ],
                    },
                    {
                        "url_list": ["https://img.example:8080/private.webp"],
                        "download_url_list": [
                            "https://img.example/original-fallback.webp"
                        ],
                    },
                ]
            }
        ]
    }

    result = douyin.DouyinParser({})._parse_slides_data(payload)

    assert result.image_urls == [
        "https://img.example/original-after-unsafe.webp",
        "https://img.example/original-fallback.webp",
    ]
