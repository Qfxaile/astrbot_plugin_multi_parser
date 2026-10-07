"""平台扩展点的统一描述与能力声明。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from .parser import Parser
from .platform_login import PlatformLoginProvider


class PlatformFeature(str, Enum):
    """平台适配器可以声明的跨平台能力。"""

    PARSE = "parse"
    LOGIN = "login"
    VIDEO = "video"
    AUDIO = "audio"
    IMAGES = "images"
    SHARE_CARD = "share_card"


ParserFactory = Callable[[Mapping[str, object]], Parser]
LoginFactory = Callable[[Mapping[str, object]], PlatformLoginProvider]


@dataclass(frozen=True)
class PlatformSpec:
    """平台解析、登录和配置装配所需的唯一元数据。"""

    parser_type: type[Parser]
    login_provider_type: type[PlatformLoginProvider] | None = None
    enabled_by_default: bool = True
    parser_priority: int = 0
    features: frozenset[PlatformFeature] = frozenset({PlatformFeature.PARSE})

    @property
    def key(self) -> str:
        """返回稳定的平台配置键。"""
        return self.parser_type.name

    @property
    def display_name(self) -> str:
        """返回用户可见的平台名称。"""
        provider = self.login_provider_type
        return str(getattr(provider or self.parser_type, "display_name", self.key))

    @property
    def cookie_config_key(self) -> str | None:
        """返回平台登录或解析使用的 Cookie 配置键。"""
        provider = self.login_provider_type or self.parser_type
        value = str(getattr(provider, "cookie_config_key", "") or "").strip()
        return value or None

    @property
    def supports_login(self) -> bool:
        return self.login_provider_type is not None


# 旧名称只作为类型别名保留，注册表的语义由 PlatformSpec 定义。
PlatformRegistration = PlatformSpec
