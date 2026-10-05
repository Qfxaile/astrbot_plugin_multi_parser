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
