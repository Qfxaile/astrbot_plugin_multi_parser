"""各平台解析与登录适配器。"""

__all__ = [
    "BilibiliParser",
    "DouyinParser",
    "FanqieParser",
    "RedBookParser",
    "TiebaParser",
    "WeiboParser",
    "WeChatParser",
    "XiaoheiheParser",
    "ZhihuParser",
    "GitHubParser",
    "QQChannelParser",
    "QzoneParser",
    "PixivParser",
]


def __getattr__(name: str):
    """从唯一平台注册表按需解析历史公开导出。"""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from .registry import PLATFORM_REGISTRY

    for registration in PLATFORM_REGISTRY:
        parser_type = registration.parser_type
        if parser_type.__name__ == name:
            globals()[name] = parser_type
            return parser_type

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
