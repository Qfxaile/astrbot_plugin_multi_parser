"""核心扩展端口和平台能力描述。"""

from .interfaces import ParserPort, PlatformClient, ResultRenderer
from .platform import PlatformFeature, PlatformSpec

__all__ = [
    "ParserPort",
    "PlatformClient",
    "PlatformFeature",
    "PlatformSpec",
    "ResultRenderer",
]
