import ast
from importlib import import_module
from importlib.util import find_spec
from pathlib import Path

import pytest
from astrbot_multi_parser.platforms.registry import PLATFORM_REGISTRY


@pytest.mark.parametrize(
    ("parser_module", "parser_name", "method_name", "content_module"),
    [
        ("bilibili", "BilibiliParser", "_get_video_info", "bilibili.video"),
        (
            "bilibili",
            "BilibiliParser",
            "_parse_bangumi_payload",
            "bilibili.bangumi",
        ),
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


def test_all_production_http_clients_include_proxy_options():
    def is_proxy_options(keyword: ast.keyword) -> bool:
        value = keyword.value
        return keyword.arg is None and (
            (isinstance(value, ast.Attribute) and value.attr == "http_client_options")
            or (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "http_client_proxy_options"
            )
        )

    project_root = Path(__file__).parents[1]
    missing_proxy_options = []
    source_paths = [
        *(project_root / "core").rglob("*.py"),
        *(project_root / "services").rglob("*.py"),
        *(
            source_path
            for registration in PLATFORM_REGISTRY
            for source_path in (
                project_root / "platforms" / registration.parser_type.name
            ).rglob("*.py")
        ),
    ]
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "AsyncClient"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx"
            ):
                continue
            if not any(is_proxy_options(keyword) for keyword in node.keywords):
                missing_proxy_options.append(
                    f"{source_path.relative_to(project_root)}:{node.lineno}"
                )

    assert missing_proxy_options == []
