import json
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace

import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.services.message_context import extract_context

CARD_URL = (
    "https://pd.qq.com/qqweb/qunpro/share?_wv=3&appChannel=share"
    "&inviteCode=PUBLIC123&attaContentID=0123456789abcdef0123456789abcdef"
    "&contentID=POST123&businessType=2"
)
COVER_URL = "https://channel.qpic.cn/psc?/channel/public-cover/c=&bo=AAQABg"
FEED_ID = "B_7e06786abc4a00001441152211141391880X60"
SECOND_IMAGE_URL = "https://channelr.photo.store.qq.com/psc?/channel/second/o="
THIRD_IMAGE_URL = "https://channelr.photo.store.qq.com/psc?/channel/third/o="
FIRST_VIDEO_URL = "https://video.qq.com/channel/first.mp4"
SECOND_VIDEO_URL = "https://video.qq.com/channel/second.mp4"
DETAIL_IMAGE_URLS = [
    f"https://channelr.photo.store.qq.com/psc?/channel/detail-{index}/o="
    for index in range(15)
]


def _feed_card_payload() -> dict:
    return {
        "app": "com.tencent.feed.lua",
        "bizsrc": "pindao.picforum",
        "prompt": "[分享帖子] 测试频道帖子",
        "meta": {
            "feed": {
                "cover": COVER_URL,
                "title": "测试频道帖子",
                "busiData": json.dumps(
                    {
                        "share_biz_data": {
                            "feed_id": FEED_ID,
                            "poster_tiny_id": "100000000000000000",
                        }
                    }
                ),
                "jumpUrl": CARD_URL,
                "ark_reserved1": "100000000000000001",
                "ark_reserved2": "100000002",
                "ark_reserved3": "B_public_feed",
            }
        },
        "config": {"token": "redacted-card-token"},
    }


def _card_context(
    *,
    url: str = CARD_URL,
    title: str = "测试频道帖子",
    preview: str = COVER_URL,
    feed_id: str = FEED_ID,
) -> ParseContext:
    return ParseContext(
        text="[ComponentType.Json]",
        json_urls=[url],
        json_previews=[preview],
        json_titles=[title],
        json_metadata=[{"feed_id": feed_id}],
    )


def _detail_payload(feed: dict) -> dict:
    return {
        "retcode": 0,
        "error": {"code": 0, "message": ""},
        "data": {"feed": feed},
    }


def _mock_detail_client(monkeypatch, handler):
    parser_module = import_module("astrbot_multi_parser.platforms.qqchannel.parser")
    real_async_client = httpx.AsyncClient

    def create_client(**kwargs):
        return real_async_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(parser_module.httpx, "AsyncClient", create_client)
    return parser_module


def test_qqchannel_feed_card_extracts_url_title_and_cover_without_token():
    event = SimpleNamespace(
        message_str="[ComponentType.Json]",
        message_obj=SimpleNamespace(
            raw_message={
                "message": [
                    {
                        "type": "json",
                        "data": {"data": json.dumps(_feed_card_payload())},
                    }
                ]
            }
        ),
    )

    context = extract_context(event)

    assert context.json_urls == [CARD_URL]
    assert context.json_titles == ["测试频道帖子"]
    assert context.json_previews == [COVER_URL]
    assert context.json_metadata == [{"feed_id": FEED_ID}]
    assert "redacted-card-token" not in context.combined_text


def test_qqchannel_platform_package_exists_and_exports_parser():
    assert find_spec("astrbot_multi_parser.platforms.qqchannel") is not None

    package = import_module("astrbot_multi_parser.platforms.qqchannel")
    assert hasattr(package, "QQChannelParser")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (CARD_URL, True),
        (
            "https://pd.qq.com/qqweb/qunpro/share?"
            "attaContentID=0123456789abcdef0123456789abcdef",
            True,
        ),
        ("https://pd.qq.com/qqweb/qunpro/share", False),
        ("http://pd.qq.com/qqweb/qunpro/share?contentID=POST123", False),
        ("https://pd.qq.com.evil.example/qqweb/qunpro/share?contentID=POST123", False),
        ("https://pd.qq.com/other/path?contentID=POST123", False),
        ("https://user@pd.qq.com/qqweb/qunpro/share?contentID=POST123", False),
    ],
)
async def test_qqchannel_matches_only_valid_json_share_cards(url, expected):
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser

    assert await parser_type({}).match(_card_context(url=url)) is expected


async def test_qqchannel_parse_fetches_detail_and_materializes_all_images(
    monkeypatch,
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("ComReader/GetFeedDetail")
        assert request.headers["Referer"] == CARD_URL
        cookie_parts = dict(
            part.strip().split("=", 1) for part in request.headers["Cookie"].split(";")
        )
        assert cookie_parts["uuid"] == cookie_parts["p_uin"]
        assert 144115351284613120 <= int(cookie_parts["uuid"]) <= 144115364169515007
        body = json.loads(request.content)
        assert body["feedId"] == FEED_ID
        assert body["channelSign"] == {}
        return httpx.Response(
            200,
            json=_detail_payload(
                {
                    "title": {
                        "contents": [{"text_content": {"text": "详情标题"}, "type": 1}]
                    },
                    "poster": {"nick": "Nex"},
                    "contents": {
                        "contents": [{"text_content": {"text": "正文内容"}, "type": 1}]
                    },
                    "images": [
                        {"picUrl": DETAIL_IMAGE_URLS[index], "display_index": index}
                        for index in reversed(range(15))
                    ],
                    "videos": [],
                    "audios": [],
                }
            ),
        )

    _mock_detail_client(monkeypatch, handler)
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser
    parser = parser_type({})
    captured = {}

    async def materialize(result, referer, *, headers=None):
        captured.update(result=result, referer=referer, headers=headers)
        return result

    monkeypatch.setattr(parser, "materialize_public_images", materialize)

    result = await parser.parse(_card_context(title="  测试\n频道帖子  "))

    assert result.platform == "qqchannel"
    assert result.title == "详情标题"
    assert result.author == "Nex"
    assert result.ordered_contents[0].kind == "text"
    assert result.ordered_contents[0].value == "正文内容"
    assert [
        item.value for item in result.ordered_contents if item.kind == "image"
    ] == DETAIL_IMAGE_URLS
    assert captured["referer"] == CARD_URL
    assert captured["headers"] == parser.HEADERS


def test_qqchannel_rich_article_preserves_text_media_and_list_order():
    content_module = import_module("astrbot_multi_parser.platforms.qqchannel.content")
    feed = {
        "title": {"contents": [{"text_content": {"text": "富文本文章"}, "type": 1}]},
        "poster": {"nick": "作者"},
        "content_with_style": {
            "paragraphs": [
                {
                    "elems": [
                        {"type": 1, "text": {"text_content": {"text": "第一段 "}}},
                        {
                            "type": 4,
                            "url": {
                                "url_content": {
                                    "displayText": "资料",
                                    "url": "https://example.com/article",
                                }
                            },
                        },
                        {
                            "type": 8,
                            "topic": {"topic_content": {"topic_name": "话题"}},
                        },
                    ]
                },
                {
                    "props": {"list_type": 2},
                    "elems": [
                        {"type": 1, "text": {"text_content": {"text": "项目一"}}}
                    ],
                },
                {
                    "elems": [
                        {
                            "type": 2,
                            "image": {
                                "image": {
                                    "picUrl": SECOND_IMAGE_URL,
                                    "picId": "image-2",
                                }
                            },
                        }
                    ]
                },
                {
                    "elems": [
                        {
                            "type": 3,
                            "video": {"video": {"playUrl": FIRST_VIDEO_URL}},
                        }
                    ]
                },
                {
                    "elems": [
                        {
                            "type": 3,
                            "video": {"video": {"playUrl": SECOND_VIDEO_URL}},
                        }
                    ]
                },
            ]
        },
        "images": [
            {
                "picUrl": SECOND_IMAGE_URL,
                "picId": "image-2",
                "display_index": 0,
            },
            {"picUrl": THIRD_IMAGE_URL, "picId": "image-3", "display_index": 1},
            {"picUrl": "https://evil.example/private.jpg", "display_index": 2},
        ],
        "videos": [
            {"playUrl": FIRST_VIDEO_URL, "display_index": 0},
            {"playUrl": SECOND_VIDEO_URL, "display_index": 1},
            {"playUrl": "https://evil.example/private.mp4", "display_index": 2},
        ],
        "audios": [],
    }

    result = content_module.build_result(feed, fallback_title="卡片标题")

    assert result.title == "富文本文章"
    assert result.author == "作者"
    assert result.video_url == FIRST_VIDEO_URL
    assert [(item.kind, item.value) for item in result.ordered_contents] == [
        ("text", "第一段 资料 (https://example.com/article)#话题"),
        ("text", "- 项目一"),
        ("image", SECOND_IMAGE_URL),
        ("text", f"视频链接: {SECOND_VIDEO_URL}"),
        ("image", THIRD_IMAGE_URL),
    ]


async def test_qqchannel_detail_business_failure_falls_back_to_card(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"retcode": 320, "error": {"code": 320, "message": "denied"}},
        )

    _mock_detail_client(monkeypatch, handler)
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser
    parser = parser_type({})

    async def materialize(result, referer, *, headers=None):
        return result

    monkeypatch.setattr(parser, "materialize_public_images", materialize)

    result = await parser.parse(_card_context())

    assert result.title == "测试频道帖子"
    assert result.cover_urls == [COVER_URL]
    assert result.extra_lines == ["帖子详情获取失败，已返回分享卡片摘要。"]


async def test_qqchannel_oversized_detail_falls_back_without_reading_body(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": str(8 * 1024 * 1024)},
            content=b"{}",
        )

    parser_module = _mock_detail_client(monkeypatch, handler)
    parser = parser_module.QQChannelParser({})

    async def materialize(result, referer, *, headers=None):
        return result

    monkeypatch.setattr(parser, "materialize_public_images", materialize)

    result = await parser.parse(_card_context())

    assert result.cover_urls == [COVER_URL]
    assert result.extra_lines == ["帖子详情获取失败，已返回分享卡片摘要。"]


async def test_qqchannel_parse_filters_untrusted_cover_without_request(monkeypatch):
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser
    parser = parser_type({})

    async def unexpected_materialize(*args, **kwargs):
        pytest.fail("不可信封面不应发起下载请求")

    monkeypatch.setattr(parser, "materialize_public_images", unexpected_materialize)

    result = await parser.parse(
        _card_context(
            title="",
            preview="https://qpic.cn.evil.example/cover.jpg",
            feed_id="",
        )
    )

    assert result.title == "腾讯频道帖子"
    assert result.cover_urls == []


async def test_qqchannel_parse_reports_missing_supported_card():
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser

    result = await parser_type({}).parse(ParseContext(text=CARD_URL))

    assert result.error == "未找到可解析的腾讯频道分享卡片。"
