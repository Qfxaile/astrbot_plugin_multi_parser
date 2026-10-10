"""应用边界使用的最小扩展接口。"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..contracts import ParseContext, ParseResult


@runtime_checkable
class ParserPort(Protocol):
    """平台解析器必须提供的异步匹配和解析能力。"""

    name: str

    async def match(self, context: ParseContext) -> bool: ...

    async def parse(self, context: ParseContext) -> ParseResult: ...


@runtime_checkable
class PlatformClient(Protocol):
    """平台 HTTP 客户端应暴露的配置和生命周期端口。"""

    def __init__(self, config: Mapping[str, object]): ...


@runtime_checkable
class PlatformAdapter(Protocol):
    """平台注册项必须提供的解析器适配器端口。"""

    name: str

    def __init__(self, config: Mapping[str, object]): ...

    async def match(self, context: ParseContext) -> bool: ...

    async def parse(self, context: ParseContext) -> ParseResult: ...


@runtime_checkable
class ResultRenderer(Protocol):
    """解析结果渲染器端口。"""

    def render(self, result: ParseResult) -> list: ...


__all__ = ["ParserPort", "PlatformAdapter", "PlatformClient", "ResultRenderer"]
