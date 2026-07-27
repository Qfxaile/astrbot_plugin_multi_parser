from astrbot_multi_parser.platforms.registry import PLATFORM_REGISTRY


def test_platform_registry_has_stable_parser_and_login_order():
    assert [registration.parser_type.name for registration in PLATFORM_REGISTRY] == [
        "bilibili",
        "douyin",
        "redbook",
        "tieba",
        "weibo",
        "wechat",
        "xiaoheihe",
        "zhihu",
        "pixiv",
    ]
    assert [
        registration.login_provider_type.display_name
        for registration in PLATFORM_REGISTRY
        if registration.login_provider_type is not None
    ] == ["B站", "抖音", "小红书", "贴吧", "微博", "微信", "小黑盒", "知乎"]


def test_platform_registry_login_providers_declare_cookie_keys():
    assert [
        registration.login_provider_type.cookie_config_key
        for registration in PLATFORM_REGISTRY
        if registration.login_provider_type is not None
    ] == [
        "bilibili_cookies",
        "douyin_cookies",
        "redbook_cookies",
        "tieba_cookies",
        "weibo_cookies",
        "wechat_yuanbao_cookies",
        "xiaoheihe_cookies",
        "zhihu_cookies",
    ]


def test_pixiv_registration_is_parser_only_and_disabled_by_default():
    registration = PLATFORM_REGISTRY[-1]

    assert registration.parser_type.name == "pixiv"
    assert registration.login_provider_type is None
    assert registration.enabled_by_default is False
