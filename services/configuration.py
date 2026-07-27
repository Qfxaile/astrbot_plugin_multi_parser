from collections.abc import Mapping

from ..core.parser import BaseParser
from ..platforms.registry import PLATFORM_REGISTRY


def build_parsers(config) -> dict[str, BaseParser]:
    """按稳定优先级创建所有平台解析器。"""
    return {
        registration.parser_type.name: registration.parser_type(config)
        for registration in PLATFORM_REGISTRY
    }


def enabled_parsers(config, parsers: Mapping[str, BaseParser]) -> list[BaseParser]:
    """按注册顺序返回当前启用的平台解析器。"""
    switches = config.get("platform_switches")
    defaults = {
        registration.parser_type.name: registration.enabled_by_default
        for registration in PLATFORM_REGISTRY
    }
    return [
        parser
        for name, parser in parsers.items()
        if bool(
            switches.get(name, defaults.get(name, True))
            if isinstance(switches, dict)
            else defaults.get(name, True)
        )
    ]
