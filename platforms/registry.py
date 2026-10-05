"""集中声明平台解析器与登录适配器的对应关系。"""

from ..core.platform import PlatformSpec
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

    display_names = [item.display_name for item in login_platforms()]
    if len(display_names) != len(set(display_names)):
        raise ValueError("平台注册表包含重复的登录显示名")

    cookie_keys = [item.cookie_config_key for item in login_platforms()]
    if any(not key for key in cookie_keys) or len(cookie_keys) != len(set(cookie_keys)):
        raise ValueError("平台注册表包含无效或重复的 Cookie 配置键")


validate_platform_registry()
