import json
import re

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.platforms import xiaoheihe
from astrbot_multi_parser.platforms.xiaoheihe import XiaoheiheParser


def test_signing_algorithm_is_exposed_by_dedicated_module():
    from astrbot_multi_parser.platforms.xiaoheihe.signing import RequestSigner

    assert RequestSigner.CHAR_TABLE == XiaoheiheParser.CHAR_TABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://www.xiaoheihe.cn/app/bbs/link/abc123",
        "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?foo=1&link_id=abc123",
        "https://www.xiaoheihe.cn/app/topic/game/pc/730",
        "https://api.xiaoheihe.cn/game/share_game_detail?appid=730&game_type=pc",
    ],
)
async def test_matches_supported_xiaoheihe_urls(url):
    assert await XiaoheiheParser({}).match(ParseContext(text=url))


@pytest.mark.asyncio
async def test_rejects_lookalike_xiaoheihe_urls():
    assert not await XiaoheiheParser({}).match(
        ParseContext(text="https://xiaoheihe.cn.evil.example/app/bbs/link/abc123")
    )


def test_extracts_only_xiaoheihe_auth_cookie_whitelist():
    parser = XiaoheiheParser(
        {
            "xiaoheihe_cookies": (
                "pkey=session-secret; x_xhh_tokenid=Bdevice123; "
                "heybox_id=123456; foreign=must-not-send"
            )
        }
    )

    assert parser._configured_cookie_values() == {
        "pkey": "session-secret",
        "x_xhh_tokenid": "Bdevice123",
    }


def test_builds_optional_cookie_headers_for_game_requests():
    parser = XiaoheiheParser(
        {
            "xiaoheihe_cookies": (
                "pkey=session-secret; x_xhh_tokenid=Bdevice123; foreign=ignore"
            )
        }
    )

    assert parser._request_cookie_headers() == {
        "Cookie": "pkey=session-secret; x_xhh_tokenid=Bdevice123"
    }
    assert XiaoheiheParser({})._request_cookie_headers() == {}


def test_signing_algorithm_matches_reference_golden_value(monkeypatch):
    parser = XiaoheiheParser({})

    assert (
        parser._ov(
            "/bbs/app/link/tree",
            1700000001,
            "ABCDEF0123456789ABCDEF0123456789",
        )
        == "V2V1Z67"
    )

    monkeypatch.setattr(xiaoheihe.time, "time", lambda: 1700000000)
    monkeypatch.setattr(xiaoheihe.random, "random", lambda: 0.5)
    result = parser._sign_path("/bbs/app/link/tree")

    assert result["_time"] == 1700000000
    assert re.fullmatch(r"[0-9A-F]{32}", str(result["nonce"]))
    assert result["hkey"] == parser._ov(
        "/bbs/app/link/tree", 1700000001, str(result["nonce"])
    )


def test_post_payload_keeps_text_and_images_in_source_order():
    payload = {
        "link": {
            "title": "帖子标题",
            "user": {"username": "盒友"},
            "text": json.dumps(
                [
                    {"type": "text", "text": "<p>第一段</p>"},
                    {
                        "type": "img",
                        "url": "https://imgheybox.max-c.com/bbs/a.jpg?token=1",
                    },
                    {
                        "type": "text",
                        "text": (
                            '<p>第二段<img data-original="https://imgheybox1.max-c.com/bbs/b.jpg?token=2">'
                            "<br>第三段</p>"
                        ),
                    },
                    {
                        "type": "img",
                        "url": "https://imgheybox1.max-c.com/bbs/a.jpg?token=duplicate",
                    },
                ]
            ),
            "has_video": True,
            "video_url": "https://video.max-c.com/bbs/post.mp4",
        }
    }

    result = XiaoheiheParser({})._parse_post_payload(payload)

    assert result.title == "帖子标题"
    assert result.author == "盒友"
    assert result.video_url == "https://video.max-c.com/bbs/post.mp4"
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一段"),
        ("image", "https://imgheybox.max-c.com/bbs/a.jpg?token=1"),
        ("text", "第二段"),
        ("image", "https://imgheybox.max-c.com/bbs/b.jpg?token=2"),
        ("text", "第三段"),
        ("image", "https://imgheybox.max-c.com/bbs/a.jpg?token=duplicate"),
    ]


def test_post_payload_rejects_missing_link():
    with pytest.raises(ValueError, match="缺少 link 节点"):
        XiaoheiheParser({})._parse_post_payload({"status": "ok"})


def install_mock_client(monkeypatch, handler):
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        xiaoheihe.parser.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )


@pytest.mark.asyncio
async def test_parse_post_requests_signed_tree_and_materializes_images(
    monkeypatch, assert_temporary_image
):
    image_bytes = b"post-image"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.xiaoheihe.cn":
            assert request.url.path == "/bbs/app/link/tree"
            assert request.url.params["link_id"] == "4e0f72248cb0"
            assert request.url.params["hkey"]
            assert request.url.params["web_version"] == "3.0"
            assert request.url.params["is_first"] == "1"
            assert request.url.params["page"] == "1"
            assert request.url.params["index"] == "1"
            assert request.url.params["limit"] == "20"
            assert request.url.params["owner_only"] == "0"
            assert request.url.params["x_client_version"] == ""
            assert "heybox_id" not in request.url.params
            assert "device_info" not in request.url.params
            assert "device_id" not in request.url.params
            assert request.headers.get("cookie") == (
                "pkey=session-secret; x_xhh_tokenid=Bdevice123"
            )
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "link": {
                            "title": "接口帖子",
                            "user": {"username": "接口作者"},
                            "text": json.dumps(
                                [
                                    {
                                        "type": "img",
                                        "url": "https://imgheybox.max-c.com/bbs/a.jpg",
                                    }
                                ]
                            ),
                        }
                    },
                },
                request=request,
            )
        assert request.url.host == "imgheybox.max-c.com"
        assert "Cookie" not in request.headers
        return httpx.Response(200, content=image_bytes, request=request)

    install_mock_client(monkeypatch, handler)
    parser = XiaoheiheParser(
        {
            "xiaoheihe_cookies": (
                "pkey=session-secret; x_xhh_tokenid=Bdevice123; foreign=must-not-send"
            )
        }
    )

    result = await parser.parse(
        ParseContext(
            text=(
                "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?"
                "h_camp=link&h_src=YXBwX3NoYXJl&link_id=4e0f72248cb0"
            )
        )
    )

    assert result.title == "接口帖子"
    assert result.author == "接口作者"
    assert_temporary_image(result, result.ordered_contents[0].value, image_bytes)
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_parse_post_reports_api_error_without_token_leak(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "error", "msg": "denied"},
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    parser = XiaoheiheParser({"xiaoheihe_cookies": "x_xhh_tokenid=Bsecret"})

    with pytest.raises(ValueError, match="Cookies 可能已失效") as exc_info:
        await parser.parse(
            ParseContext(text="https://www.xiaoheihe.cn/app/bbs/link/abc123")
        )

    assert "Bsecret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_parse_post_reports_captcha_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "show_captcha", "result": {}},
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    parser = XiaoheiheParser({})

    with pytest.raises(ValueError, match="人机验证"):
        await parser.parse(
            ParseContext(
                text=(
                    "https://api.xiaoheihe.cn/v3/bbs/app/api/web/share?"
                    "h_camp=link&h_src=YXBwX3NoYXJl&link_id=4e0f72248cb0"
                )
            )
        )


GAME_HTML = """
<html>
  <div class="row-2"><div class="tags">
    <div class="tag common"><span>射击</span><span>多人</span></div>
    <p class="tag">合作</p>
  </div></div>
  <script id="__NUXT_DATA__" type="application/json">
  [{"game":1},{"appid":"730","steam_appid":"730","name":"反恐精英","name_en":"Counter-Strike 2","score":"9.2","comment_stats":{"score_comment":12345},"screenshots":[{"url":"https://gameimg.max-c.com/screenshot.jpg"}],"video_url":"https://video.max-c.com/game.mp4","video_thumb":"https://gameimg.max-c.com/thumb.jpg"}]
  </script>
</html>
"""


def test_nuxt_state_resolves_game_and_extracts_metadata():
    parser = XiaoheiheParser({})

    result = parser._parse_game_state(
        GAME_HTML,
        "730",
        "pc",
        {
            "about_the_game": "一款合作游戏",
            "release_date": "2020-01-02",
            "developers": [{"value": "开发商"}],
            "publishers": [{"value": "发行商"}],
        },
    )

    assert result.title == "反恐精英（Counter-Strike 2）"
    assert result.description.startswith("一款合作游戏")
    assert "类型：[ 射击 多人 ] [ 合作 ]" in result.description
    assert "小黑盒评分：9.2（1.2 万人评价）" in result.description
    assert result.image_urls == ["https://gameimg.max-c.com/screenshot.jpg"]
    assert result.video_url == "https://video.max-c.com/game.mp4"


def test_nuxt_state_rejects_missing_game():
    with pytest.raises(ValueError, match="未找到游戏详情数据"):
        XiaoheiheParser({})._parse_game_state(
            '<script id="__NUXT_DATA__">[{"not_game":1},{"name":"其他"}]</script>',
            "730",
            "pc",
            {},
        )


def test_game_helpers_deduplicate_images_and_format_prices():
    parser = XiaoheiheParser({})
    game = {
        "appid": "730",
        "name": "游戏",
        "screenshots": [
            {"url": "https://gameimg.max-c.com/a.jpg?x=1"},
            {"url": "https://gameimg.max-c.com/a.jpg?x=2"},
        ],
        "price": {"initial": "¥ 100", "lowest_price": "80"},
        "heybox_price": {"cost_coin": 1250},
    }

    assert parser._extract_game_images(game, "") == [
        "https://gameimg.max-c.com/a.jpg?x=1"
    ]
    assert parser._format_yuan_from_coin(1250) == "1.25"
    assert "价格：¥ 100" in parser._build_game_desc("", game, {})
    assert "史低价格：¥ 80" in parser._build_game_desc("", game, {})
    assert "当前价格：¥ 1.25" in parser._build_game_desc("", game, {})


@pytest.mark.asyncio
async def test_parse_game_page_merges_intro_and_materializes_images(
    monkeypatch, assert_temporary_image
):
    image_bytes = b"game-image"
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "api.xiaoheihe.cn":
            if request.url.path == "/game/get_game_detail/":
                assert request.url.params["steam_appid"] == "991d6993109c"
                assert request.url.params["hkey"]
                assert request.headers.get("cookie") == "x_xhh_tokenid=Bdevice123"
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "result": {
                            "appid": 1071870,
                            "steam_appid": 1071870,
                            "name": "只只大冒险",
                            "name_en": "Biped",
                            "score": "8.7",
                            "comment_stats": {"score_comment": 2212},
                            "common_tags": [
                                {
                                    "type": "steam_aggre",
                                    "desc_list": ["中文", "单人/多人"],
                                },
                                {"type": "simple_tag", "desc": "动作"},
                            ],
                            "screenshots": [
                                {
                                    "type": "movie",
                                    "thumbnail": (
                                        "https://gameimg.max-c.com/trailer.jpg"
                                    ),
                                    "url": "https://video.max-c.com/game.m3u8",
                                },
                                {
                                    "type": "image",
                                    "url": ("https://gameimg.max-c.com/screenshot.jpg"),
                                },
                            ],
                        },
                    },
                    request=request,
                )
            if request.url.path == "/game/game_introduction/":
                assert request.url.params["steam_appid"] == "1071870"
                return httpx.Response(
                    200,
                    json={
                        "status": "ok",
                        "result": {
                            "about_the_game": (
                                '<video><source src="https://video.max-c.com/'
                                'game.mp4?token=1" type="video/mp4"></video>'
                            ),
                            "release_date": "2020-01-02",
                            "developers": [{"value": "开发商"}],
                            "publishers": [{"value": "发行商"}],
                        },
                    },
                    request=request,
                )
        assert request.url.host == "gameimg.max-c.com"
        assert "Cookie" not in request.headers
        return httpx.Response(200, content=image_bytes, request=request)

    install_mock_client(monkeypatch, handler)
    result = await XiaoheiheParser(
        {"xiaoheihe_cookies": "x_xhh_tokenid=Bdevice123"}
    ).parse(
        ParseContext(
            text=(
                "https://api.xiaoheihe.cn/game/share_game_detail?"
                "appid=991d6993109c&game_type=pc"
            )
        )
    )

    assert result.title == "只只大冒险（Biped）"
    assert "开发商：开发商" in result.description
    assert "类型：[ 中文 单人/多人 ] [ 动作 ]" in result.description
    assert "小黑盒评分：8.7（2212 人评价）" in result.description
    assert result.video_url == "https://video.max-c.com/game.mp4?token=1"
    assert "备用视频: https://video.max-c.com/game.m3u8" in result.extra_lines
    assert len(result.image_urls) == 2
    assert_temporary_image(result, result.image_urls[0], image_bytes)
    assert len(requests) == 4


@pytest.mark.asyncio
async def test_parse_game_reports_detail_api_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "error", "msg": "denied"},
            request=request,
        )

    install_mock_client(monkeypatch, handler)
    parser = XiaoheiheParser({"xiaoheihe_cookies": "x_xhh_tokenid=Bdevice123"})

    with pytest.raises(ValueError, match="Cookies 可能已失效"):
        await parser.parse(
            ParseContext(text="https://www.xiaoheihe.cn/app/topic/game/pc/730")
        )
