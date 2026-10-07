import json
from pathlib import Path

import pytest
from astrbot_multi_parser.core.parser import BaseParser
from astrbot_multi_parser.core.ports import PlatformFeature, PlatformSpec
from astrbot_multi_parser.platforms.registry import (
    PLATFORM_REGISTRY,
    login_platforms,
    parser_platforms,
    validate_platform_configuration,
    validate_platform_registry,
)


def test_platform_registry_has_stable_parser_and_login_order():
    assert [registration.key for registration in parser_platforms()] == [
        "bilibili",
        "douyin",
        "fanqie",
        "redbook",
        "tieba",
        "weibo",
        "wechat",
        "xiaoheihe",
        "zhihu",
        "github",
        "qqchannel",
        "qzone",
        "pixiv",
    ]
    assert [
        registration.login_provider_type.display_name
        for registration in login_platforms()
    ] == ["B站", "抖音", "小红书", "贴吧", "微博", "微信", "小黑盒", "知乎"]


def test_platform_registry_login_providers_declare_cookie_keys():
    assert [
        registration.login_provider_type.cookie_config_key
        for registration in login_platforms()
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


def test_platform_registry_declares_parse_and_login_capabilities():
    for registration in PLATFORM_REGISTRY:
        assert registration.supports(PlatformFeature.PARSE)
        assert registration.supports(PlatformFeature.LOGIN) is (
            registration.login_provider_type is not None
        )


def test_platform_registry_rejects_inconsistent_login_capability(monkeypatch):
    from astrbot_multi_parser.platforms import registry

    parser_type = PLATFORM_REGISTRY[0].parser_type
    monkeypatch.setattr(
        registry,
        "PLATFORM_REGISTRY",
        (
            PlatformSpec(
                parser_type,
                None,
                features=frozenset({PlatformFeature.PARSE, PlatformFeature.LOGIN}),
            ),
        ),
    )

    with pytest.raises(ValueError, match="login 能力声明"):
        registry.validate_platform_registry()


def test_login_provider_proxy_names_match_registered_parsers():
    assert all(
        registration.login_provider_type is None
        or registration.login_provider_type.name == registration.parser_type.name
        for registration in PLATFORM_REGISTRY
    )


def test_pixiv_registration_is_parser_only_and_disabled_by_default():
    registration = PLATFORM_REGISTRY[-1]

    assert registration.parser_type.name == "pixiv"
    assert registration.login_provider_type is None
    assert registration.enabled_by_default is False


def test_platform_registry_is_self_consistent():
    validate_platform_registry()


def test_platform_registry_matches_configuration_schema():
    schema = json.loads(
        (Path(__file__).parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    validate_platform_configuration(schema)


def test_registered_parsers_implement_parser_contract():
    for registration in PLATFORM_REGISTRY:
        parser_type = registration.parser_type
        assert parser_type.match is not BaseParser.match
        assert parser_type.parse is not BaseParser.parse


def test_parser_registry_rejects_non_async_contract(monkeypatch):
    from astrbot_multi_parser.core.ports import PlatformSpec
    from astrbot_multi_parser.platforms import registry

    class InvalidParser(BaseParser):
        name = "invalid"

        def match(self, context):
            return True

        def parse(self, context):
            return None

    monkeypatch.setattr(registry, "PLATFORM_REGISTRY", (PlatformSpec(InvalidParser),))

    with pytest.raises(ValueError, match="必须是异步方法"):
        registry.validate_platform_registry()


@pytest.mark.parametrize("platform", ["fanqie", "qqchannel", "qzone"])
def test_public_parser_only_platforms_are_enabled_by_default(platform):
    registration = next(
        item for item in PLATFORM_REGISTRY if item.parser_type.name == platform
    )

    assert registration.login_provider_type is None
    assert registration.enabled_by_default is True
