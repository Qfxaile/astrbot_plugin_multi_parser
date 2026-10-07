"""集中声明平台解析器与登录适配器的对应关系。"""

import inspect
from collections.abc import Mapping

from ..core.platform import PlatformFeature, PlatformSpec
from .bilibili import BilibiliLoginProvider, BilibiliParser
from .douyin import DouyinLoginProvider, DouyinParser
from .fanqie import FanqieParser
from .github import GitHubParser
from .pixiv import PixivParser
from .qqchannel import QQChannelParser
from .qzone import QzoneParser
from .redbook import RedBookLoginProvider, RedBookParser
from .tieba import TiebaLoginProvider, TiebaParser
from .wechat import WeChatLoginProvider, WeChatParser
from .weibo import WeiboLoginProvider, WeiboParser
from .xiaoheihe import XiaoheiheLoginProvider, XiaoheiheParser
from .zhihu import ZhihuLoginProvider, ZhihuParser

PlatformRegistration = PlatformSpec


PLATFORM_REGISTRY: tuple[PlatformSpec, ...] = (
    PlatformRegistration(
        BilibiliParser,
        BilibiliLoginProvider,
        parser_priority=1,
    ),
    PlatformRegistration(DouyinParser, DouyinLoginProvider),
    PlatformRegistration(FanqieParser, None),
    PlatformRegistration(RedBookParser, RedBookLoginProvider),
    PlatformRegistration(TiebaParser, TiebaLoginProvider),
    PlatformRegistration(WeiboParser, WeiboLoginProvider),
    PlatformRegistration(WeChatParser, WeChatLoginProvider),
    PlatformRegistration(XiaoheiheParser, XiaoheiheLoginProvider),
    PlatformRegistration(ZhihuParser, ZhihuLoginProvider),
    PlatformRegistration(GitHubParser, None),
    PlatformRegistration(QQChannelParser, None),
    PlatformRegistration(QzoneParser, None),
    PlatformRegistration(PixivParser, None, enabled_by_default=False),
)


def parser_platforms() -> tuple[PlatformSpec, ...]:
    """返回注册顺序的平台描述，顺序本身是解析优先级契约。"""
    return PLATFORM_REGISTRY


def login_platforms() -> tuple[PlatformSpec, ...]:
    """返回声明登录 Provider 的平台描述，保持注册顺序。"""
    return tuple(item for item in PLATFORM_REGISTRY if item.login_provider_type)


def validate_platform_registry() -> None:
    """校验平台键、显示名和登录 Cookie 键没有冲突。"""
    keys = [item.key for item in PLATFORM_REGISTRY]
    if len(keys) != len(set(keys)):
        raise ValueError("平台注册表包含重复的解析键")

    for item in PLATFORM_REGISTRY:
        parser_type = item.parser_type
        if not callable(getattr(parser_type, "match", None)) or not callable(
            getattr(parser_type, "parse", None)
        ):
            raise ValueError(f"{item.key} 缺少 match 或 parse 解析接口")
        if not inspect.iscoroutinefunction(
            parser_type.match
        ) or not inspect.iscoroutinefunction(parser_type.parse):
            raise ValueError(f"{item.key} 的 match 和 parse 必须是异步方法")
        if PlatformFeature.PARSE not in item.features:
            raise ValueError(f"{item.key} 必须声明 parse 能力")

    display_names = [item.display_name for item in login_platforms()]
    if len(display_names) != len(set(display_names)):
        raise ValueError("平台注册表包含重复的登录显示名")

    cookie_keys = [item.cookie_config_key for item in login_platforms()]
    if any(not key for key in cookie_keys) or len(cookie_keys) != len(set(cookie_keys)):
        raise ValueError("平台注册表包含无效或重复的 Cookie 配置键")


def validate_platform_configuration(schema: Mapping[str, object]) -> None:
    """校验配置 Schema 的平台开关和登录 Cookie 键与注册表一致。"""
    switches = schema.get("platform_switches")
    switch_items = switches.get("items") if isinstance(switches, Mapping) else None
    registered_keys = {item.key for item in parser_platforms()}
    configured_keys = set(switch_items) if isinstance(switch_items, Mapping) else set()
    if configured_keys != registered_keys:
        raise ValueError(
            "平台开关配置与平台注册表不一致: "
            f"缺少={sorted(registered_keys - configured_keys)}, "
            f"多余={sorted(configured_keys - registered_keys)}"
        )

    cookies = schema.get("cookies")
    cookie_items = cookies.get("items") if isinstance(cookies, Mapping) else None
    configured_cookies = (
        set(cookie_items) if isinstance(cookie_items, Mapping) else set()
    )
    registered_cookies = {
        item.cookie_config_key for item in login_platforms() if item.cookie_config_key
    }
    if configured_cookies != registered_cookies:
        raise ValueError(
            "平台 Cookie 配置与平台注册表不一致: "
            f"缺少={sorted(registered_cookies - configured_cookies)}, "
            f"多余={sorted(configured_cookies - registered_cookies)}"
        )


validate_platform_registry()
