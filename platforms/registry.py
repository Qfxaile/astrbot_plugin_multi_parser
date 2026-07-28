"""集中声明平台解析器与登录适配器的对应关系。"""

from dataclasses import dataclass

from ..core.parser import BaseParser
from ..core.platform_login import PlatformLoginProvider
from .bilibili import BilibiliLoginProvider, BilibiliParser
from .douyin import DouyinLoginProvider, DouyinParser
from .github import GitHubParser
from .pixiv import PixivParser
from .redbook import RedBookLoginProvider, RedBookParser
from .tieba import TiebaLoginProvider, TiebaParser
from .wechat import WeChatLoginProvider, WeChatParser
from .weibo import WeiboLoginProvider, WeiboParser
from .xiaoheihe import XiaoheiheLoginProvider, XiaoheiheParser
from .zhihu import ZhihuLoginProvider, ZhihuParser


@dataclass(frozen=True)
class PlatformRegistration:
    """描述同一平台的解析与登录入口。"""

    parser_type: type[BaseParser]
    login_provider_type: type[PlatformLoginProvider] | None
    enabled_by_default: bool = True


PLATFORM_REGISTRY: tuple[PlatformRegistration, ...] = (
    PlatformRegistration(BilibiliParser, BilibiliLoginProvider),
    PlatformRegistration(DouyinParser, DouyinLoginProvider),
    PlatformRegistration(RedBookParser, RedBookLoginProvider),
    PlatformRegistration(TiebaParser, TiebaLoginProvider),
    PlatformRegistration(WeiboParser, WeiboLoginProvider),
    PlatformRegistration(WeChatParser, WeChatLoginProvider),
    PlatformRegistration(XiaoheiheParser, XiaoheiheLoginProvider),
    PlatformRegistration(ZhihuParser, ZhihuLoginProvider),
    PlatformRegistration(GitHubParser, None),
    PlatformRegistration(PixivParser, None, enabled_by_default=False),
)
