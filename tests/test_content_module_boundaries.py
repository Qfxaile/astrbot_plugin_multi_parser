from importlib import import_module
from importlib.util import find_spec

import pytest


@pytest.mark.parametrize(
    ("parser_module", "parser_name", "method_name", "content_module"),
    [
        ("bilibili", "BilibiliParser", "_get_video_info", "bilibili.video"),
        ("bilibili", "BilibiliParser", "_parse_live_payload", "bilibili.live"),
        ("bilibili", "BilibiliParser", "_parse_mall", "bilibili.mall"),
        (
            "bilibili",
            "BilibiliParser",
            "_parse_dynamic_payload",
            "bilibili.dynamic",
        ),
        (
            "bilibili",
            "BilibiliParser",
            "_parse_article_payload",
            "bilibili.article",
        ),
        ("douyin", "DouyinParser", "_parse_live_data", "douyin.live"),
        ("douyin", "DouyinParser", "_parse_router_data", "douyin.work"),
        ("douyin", "DouyinParser", "_parse_video_item", "douyin.video"),
        ("douyin", "DouyinParser", "_parse_gallery_item", "douyin.gallery"),
        ("douyin", "DouyinParser", "_parse_slides_data", "douyin.gallery"),
        ("douyin", "DouyinParser", "_parse_shop_url", "douyin.shop"),
        ("weibo", "WeiboParser", "_parse_status_payload", "weibo.post"),
        ("weibo", "WeiboParser", "_parse_article_payload", "weibo.article"),
        ("weibo", "WeiboParser", "_parse_video_payload", "weibo.video"),
        ("redbook", "RedBookParser", "_select_video_url", "redbook.video"),
        (
            "redbook",
            "RedBookParser",
            "_select_original_image_url",
            "redbook.gallery",
        ),
        ("tieba", "TiebaParser", "_parse_page", "tieba.thread"),
        ("wechat", "WeChatParser", "_parse_article", "wechat.article"),
        ("wechat", "WeChatParser", "_parse_channels", "wechat.channels"),
        ("xiaoheihe", "XiaoheiheParser", "_parse_post_by_id", "xiaoheihe.post"),
        ("xiaoheihe", "XiaoheiheParser", "_parse_game_by_appid", "xiaoheihe.game"),
        ("zhihu", "ZhihuParser", "_parse_url", "zhihu.resolver"),
    ],
)
def test_parser_content_methods_are_owned_by_content_modules(
    parser_module,
    parser_name,
    method_name,
    content_module,
):
    module = import_module(f"astrbot_multi_parser.platforms.{parser_module}")
    parser_type = getattr(module, parser_name)

    assert getattr(parser_type, method_name).__module__.endswith(content_module)


@pytest.mark.parametrize(
    ("module_name", "function_name"),
    [
        ("zhihu.answer", "parse_answer_payload"),
        ("zhihu.question", "parse_question_payload"),
        ("zhihu.article", "parse_article_payload"),
        ("zhihu.pin", "parse_pin_payload"),
        ("wechat.article", "parse_article_html"),
        ("wechat.channels", "resolve_channels_share"),
    ],
)
def test_content_handlers_are_exported_by_dedicated_modules(
    module_name,
    function_name,
):
    module = import_module(f"astrbot_multi_parser.platforms.{module_name}")

    assert getattr(module, function_name).__module__ == module.__name__


def test_xiaoheihe_shared_helpers_are_owned_by_common_module():
    common_module_name = "astrbot_multi_parser.platforms.xiaoheihe.common"

    assert find_spec(common_module_name) is not None

    common_module = import_module(common_module_name)
    post_module = import_module("astrbot_multi_parser.platforms.xiaoheihe.post")
    game_module = import_module("astrbot_multi_parser.platforms.xiaoheihe.game")
    expected_helpers = {
        post_module: ("clean_text", "normalize_media_url", "normalize_image_url"),
        game_module: (
            "clean_text",
            "normalize_media_url",
            "normalize_image_url",
            "image_dedup_key",
        ),
    }
    for content_module, helper_names in expected_helpers.items():
        for helper_name in helper_names:
            helper = getattr(common_module, helper_name)
            assert helper.__module__ == common_module_name
            assert getattr(content_module, helper_name) is helper


def test_zhihu_result_helpers_use_descriptive_module_name():
    module_prefix = "astrbot_multi_parser.platforms.zhihu"

    assert find_spec(f"{module_prefix}._result_builder") is not None
    assert find_spec(f"{module_prefix}._handler_common") is None
