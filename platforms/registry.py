"""集中声明平台解析器与登录适配器的对应关系。"""

import inspect
from collections.abc import Mapping

from ..core.parser import BaseParser
from ..core.ports import ParseTypeSpec, PlatformFeature, PlatformSpec
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

PLATFORM_REGISTRY: tuple[PlatformSpec, ...] = (
    PlatformSpec(
        BilibiliParser,
        BilibiliLoginProvider,
        parser_priority=1,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.VIDEO,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("video", "视频"),
            ParseTypeSpec("dynamic", "动态/图文"),
            ParseTypeSpec("article", "专栏文章"),
            ParseTypeSpec("live", "直播间"),
            ParseTypeSpec("bangumi", "番剧/影视介绍"),
            ParseTypeSpec("mall", "会员购/票务/商品"),
        ),
    ),
    PlatformSpec(
        DouyinParser,
        DouyinLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.VIDEO,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("video", "视频"),
            ParseTypeSpec("image_text", "图文"),
            ParseTypeSpec("live", "直播"),
            ParseTypeSpec("product", "商品"),
            ParseTypeSpec("music", "汽水音乐"),
        ),
    ),
    PlatformSpec(
        FanqieParser,
        None,
        features=frozenset({PlatformFeature.PARSE, PlatformFeature.IMAGES}),
        parse_type_controls_ready=True,
        parse_types=(ParseTypeSpec("novel", "小说分享"),),
    ),
    PlatformSpec(
        RedBookParser,
        RedBookLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("video_note", "视频笔记"),
            ParseTypeSpec("image_note", "图文笔记"),
        ),
    ),
    PlatformSpec(
        TiebaParser,
        TiebaLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(ParseTypeSpec("post", "帖子"),),
    ),
    PlatformSpec(
        WeiboParser,
        WeiboLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.VIDEO,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("post", "微博/转发"),
            ParseTypeSpec("article", "长文章"),
            ParseTypeSpec("video", "独立视频"),
        ),
    ),
    PlatformSpec(
        WeChatParser,
        WeChatLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("article", "公众号文章"),
            ParseTypeSpec("channels", "视频号"),
        ),
    ),
    PlatformSpec(
        XiaoheiheParser,
        XiaoheiheLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("post", "社区帖子"),
            ParseTypeSpec("game", "游戏信息"),
        ),
    ),
    PlatformSpec(
        ZhihuParser,
        ZhihuLoginProvider,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.LOGIN,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(
            ParseTypeSpec("question_answer", "问题/回答"),
            ParseTypeSpec("article", "文章"),
            ParseTypeSpec("idea", "想法"),
        ),
    ),
    PlatformSpec(
        GitHubParser,
        None,
        features=frozenset({PlatformFeature.PARSE, PlatformFeature.IMAGES}),
        parse_type_controls_ready=True,
        parse_types=(ParseTypeSpec("repository", "仓库主页"),),
    ),
    PlatformSpec(
        QQChannelParser,
        None,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.VIDEO,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(ParseTypeSpec("post", "频道帖子"),),
    ),
    PlatformSpec(
        QzoneParser,
        None,
        features=frozenset(
            {
                PlatformFeature.PARSE,
                PlatformFeature.VIDEO,
                PlatformFeature.IMAGES,
            }
        ),
        parse_type_controls_ready=True,
        parse_types=(ParseTypeSpec("post", "公开说说"),),
    ),
    PlatformSpec(
        PixivParser,
        None,
        enabled_by_default=False,
        features=frozenset({PlatformFeature.PARSE, PlatformFeature.IMAGES}),
        parse_type_controls_ready=True,
        parse_types=(ParseTypeSpec("illustration", "插画作品"),),
    ),
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
        parse_type_keys = [parse_type.key for parse_type in item.parse_types]
        if not parse_type_keys or len(parse_type_keys) != len(set(parse_type_keys)):
            raise ValueError(f"{item.key} 的解析类型键为空或重复")
        if any(
            not key.isascii() or not key.replace("_", "").isalnum()
            for key in parse_type_keys
        ):
            raise ValueError(f"{item.key} 的解析类型键格式无效")
        if any(not parse_type.display_name.strip() for parse_type in item.parse_types):
            raise ValueError(f"{item.key} 的解析类型显示名不能为空")
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
    """校验原生插件配置中的平台开关和登录 Cookie 配置。"""
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
