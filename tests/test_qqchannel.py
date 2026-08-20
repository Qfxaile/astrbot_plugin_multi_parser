import json
from importlib import import_module
from importlib.util import find_spec
from types import SimpleNamespace

import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.services.message_context import extract_context

CARD_URL = (
    "https://pd.qq.com/qqweb/qunpro/share?_wv=3&appChannel=share"
    "&inviteCode=PUBLIC123&attaContentID=0123456789abcdef0123456789abcdef"
    "&contentID=POST123&businessType=2"
)
COVER_URL = "https://channel.qpic.cn/psc?/channel/public-cover/c=&bo=AAQABg"


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
                            "feed_id": "B_public_feed",
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
) -> ParseContext:
    return ParseContext(
        text="[ComponentType.Json]",
        json_urls=[url],
        json_previews=[preview],
        json_titles=[title],
    )


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


async def test_qqchannel_parse_uses_card_metadata_and_materializes_trusted_cover(
    monkeypatch,
):
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
    assert result.title == "测试 频道帖子"
    assert result.cover_urls == [COVER_URL]
    assert captured["referer"] == CARD_URL
    assert captured["headers"]["User-Agent"]


async def test_qqchannel_parse_filters_untrusted_cover_without_request(monkeypatch):
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser
    parser = parser_type({})

    async def unexpected_materialize(*args, **kwargs):
        pytest.fail("不可信封面不应发起下载请求")

    monkeypatch.setattr(parser, "materialize_public_images", unexpected_materialize)

    result = await parser.parse(
        _card_context(title="", preview="https://qpic.cn.evil.example/cover.jpg")
    )

    assert result.title == "腾讯频道帖子"
    assert result.cover_urls == []


async def test_qqchannel_parse_reports_missing_supported_card():
    parser_type = import_module(
        "astrbot_multi_parser.platforms.qqchannel"
    ).QQChannelParser

    result = await parser_type({}).parse(ParseContext(text=CARD_URL))

    assert result.error == "未找到可解析的腾讯频道分享卡片。"
