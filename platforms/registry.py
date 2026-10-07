"""集中声明平台解析器与登录适配器的对应关系。"""

import inspect
from collections.abc import Mapping

from ..core.parser import BaseParser
from ..core.ports import PlatformFeature, PlatformSpec
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

_LOGIN_FEATURES = frozenset({PlatformFeature.PARSE, PlatformFeature.LOGIN})


PLATFORM_REGISTRY: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        BilibiliParser,
        BilibiliLoginProvider,
        parser_priority=1,
        features=_LOGIN_FEATURES,
    ),
    PlatformSpec(DouyinParser, DouyinLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(FanqieParser, None),
    PlatformSpec(RedBookParser, RedBookLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(TiebaParser, TiebaLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(WeiboParser, WeiboLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(WeChatParser, WeChatLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(XiaoheiheParser, XiaoheiheLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(ZhihuParser, ZhihuLoginProvider, features=_LOGIN_FEATURES),
    PlatformSpec(GitHubParser, None),
    PlatformSpec(QQChannelParser, None),
    PlatformSpec(QzoneParser, None),
    PlatformSpec(PixivParser, None, enabled_by_default=False),
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
        if not inspect.isclass(parser_type) or not issubclass(parser_type, BaseParser):
            raise ValueError(f"{item.key} 必须继承 BaseParser")
        if not inspect.isclass(parser_type) or not issubclass(parser_type, BaseParser):
            raise ValueError(f"{item.key} 必须继承 BaseParser")
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
        if item.supports_login != item.supports(PlatformFeature.LOGIN):
            raise ValueError(f"{item.key} 的 login 能力声明与 Provider 不一致")
        if item.login_provider_type is not None and not inspect.isclass(
            item.login_provider_type
        ):
            raise ValueError(f"{item.key} 的登录 Provider 必须是类")

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
