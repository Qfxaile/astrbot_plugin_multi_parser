"""验证解析类型开关与各平台原有的路由顺序。"""

from unittest.mock import AsyncMock, patch

import pytest
from astrbot_multi_parser.core.contracts import ParseContext, ParseResult
from astrbot_multi_parser.platforms.bilibili.parser import BilibiliParser
from astrbot_multi_parser.platforms.douyin.parser import DouyinParser
from astrbot_multi_parser.platforms.redbook.parser import RedBookParser
from astrbot_multi_parser.platforms.registry import PLATFORM_REGISTRY
from astrbot_multi_parser.platforms.wechat.parser import WeChatParser
from astrbot_multi_parser.platforms.weibo.parser import WeiboParser
from astrbot_multi_parser.platforms.xiaoheihe.parser import XiaoheiheParser
from astrbot_multi_parser.platforms.zhihu.parser import ZhihuParser

ARTICLE = "https://mp.weixin.qq.com/s/sample"
CHANNELS = "https://weixin.qq.com/sph/sample"


@pytest.mark.asyncio
async def test_wechat_skips_disabled_type_and_preserves_article_priority(monkeypatch):
    switches = {"article": False}
    parser = WeChatParser({"platform_feature_switches": {"wechat": switches}})
    article = AsyncMock(return_value=ParseResult(platform="wechat"))
    channels = AsyncMock(return_value=ParseResult(platform="wechat"))
    monkeypatch.setattr(parser, "_parse_article", article)
    monkeypatch.setattr(parser, "_parse_channels", channels)

    assert not await parser.match(ParseContext(text=ARTICLE))
    context = ParseContext(text=f"{ARTICLE} {CHANNELS}")
    assert await parser.match(context)
    assert await parser.parse(context) is channels.return_value
    article.assert_not_awaited()
    switches["article"] = True
    reverse = ParseContext(text=f"{CHANNELS} {ARTICLE}")
    assert await parser.parse(reverse) is article.return_value


async def test_douyin_skips_disabled_video_before_live(monkeypatch):
    parser = DouyinParser({"platform_feature_switches": {"douyin": {"video": False}}})
    live = AsyncMock(return_value=ParseResult(platform="douyin"))
    monkeypatch.setattr(parser, "_parse_live", live)
    context = ParseContext(
        text="https://www.douyin.com/video/123 https://live.douyin.com/456"
    )

    assert await parser.match(context)
    assert await parser.parse(context) is live.return_value
    assert live.await_args.args[1] == "456"


async def test_douyin_does_not_match_note_when_both_media_types_are_disabled():
    parser = DouyinParser(
        {"platform_feature_switches": {"douyin": {"video": False, "image_text": False}}}
    )

    assert not await parser.match(ParseContext(text="https://www.douyin.com/note/123"))


async def test_zhihu_skips_disabled_question_before_article(monkeypatch):
    parser = ZhihuParser(
        {"platform_feature_switches": {"zhihu": {"question_answer": False}}}
    )
    article_url = "https://zhuanlan.zhihu.com/p/456"
    article = AsyncMock(return_value=ParseResult(platform="zhihu"))
    monkeypatch.setattr(parser, "_parse_url", article)
    monkeypatch.setattr(
        parser, "materialize_images", AsyncMock(side_effect=lambda result, *_: result)
    )
    context = ParseContext(text=f"https://www.zhihu.com/question/123 {article_url}")

    assert await parser.match(context)
    assert await parser.parse(context) is article.return_value
    assert article.await_args.args[2] == article_url


async def test_bilibili_disabled_video_does_not_mask_live_short_link(monkeypatch):
    parser = BilibiliParser(
        {"platform_feature_switches": {"bilibili": {"video": False}}}
    )
    live = AsyncMock(return_value=ParseResult(platform="bilibili"))
    monkeypatch.setattr(parser, "_parse_live", live)
    context = ParseContext(
        text="https://www.bilibili.com/video/BV1xx411c7mD https://b23.tv/test"
    )

    with patch(
        "astrbot_multi_parser.platforms.bilibili.parser.httpx.AsyncClient"
    ) as client:
        get = client.return_value.__aenter__.return_value.get
        get.return_value.url = "https://live.bilibili.com/456"
        assert await parser.match(context)
        assert await parser.parse(context) is live.return_value

    live.assert_awaited_once_with("456")


@pytest.mark.parametrize(
    ("parser_type", "platform", "feature", "url"),
    [
        (
            BilibiliParser,
            "bilibili",
            "video",
            "https://www.bilibili.com/video/BV1xx411c7mD",
        ),
        (BilibiliParser, "bilibili", "article", "https://www.bilibili.com/read/cv123"),
        (DouyinParser, "douyin", "video", "https://www.douyin.com/video/123"),
        (
            DouyinParser,
            "douyin",
            "image_text",
            "https://www.douyin.com/iesdouyin/share/slides/123/",
        ),
        (DouyinParser, "douyin", "live", "https://live.douyin.com/123"),
        (
            DouyinParser,
            "douyin",
            "product",
            "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?goods_id=123",
        ),
        (
            DouyinParser,
            "douyin",
            "music",
            "https://music.douyin.com/qishui/share/track?track_id=123",
        ),
        (WeiboParser, "weibo", "post", "https://weibo.com/123/AbCd"),
        (
            WeiboParser,
            "weibo",
            "article",
            "https://card.weibo.com/article/m/show/id/123",
        ),
        (WeiboParser, "weibo", "video", "https://video.weibo.com/show?fid=1034:123"),
        (
            XiaoheiheParser,
            "xiaoheihe",
            "post",
            "https://www.xiaoheihe.cn/app/bbs/link/abc",
        ),
        (
            XiaoheiheParser,
            "xiaoheihe",
            "game",
            "https://www.xiaoheihe.cn/app/topic/game/steam/123",
        ),
        (
            ZhihuParser,
            "zhihu",
            "question_answer",
            "https://www.zhihu.com/question/123/answer/456",
        ),
        (ZhihuParser, "zhihu", "article", "https://zhuanlan.zhihu.com/p/123"),
        (ZhihuParser, "zhihu", "idea", "https://www.zhihu.com/pin/123"),
    ],
)
async def test_platform_match_skips_disabled_content_type(
    parser_type, platform, feature, url
):
    parser = parser_type({"platform_feature_switches": {platform: {feature: False}}})

    assert not await parser.match(ParseContext(text=url))


@pytest.mark.parametrize(
    ("url", "feature"),
    [
        ("https://www.xiaohongshu.com/explore/note123", "image_note"),
        ("https://www.xiaohongshu.com/explore/note123", "video_note"),
    ],
)
async def test_redbook_same_route_remains_candidate_if_one_note_type_enabled(
    url, feature
):
    parser = RedBookParser(
        {
            "platform_feature_switches": {
                "redbook": {"image_note": False, "video_note": False, feature: True}
            }
        }
    )

    assert await parser.match(ParseContext(text=url))


async def test_douyin_note_route_waits_for_payload_to_identify_media_type():
    parser = DouyinParser({"platform_feature_switches": {"douyin": {"video": False}}})
    url = "https://www.douyin.com/note/123"

    assert parser._parse_type(url) is None
    assert await parser.match(ParseContext(text=url))


async def test_all_registered_platform_controls_are_connected():
    assert all(item.parse_type_controls_ready for item in PLATFORM_REGISTRY)
