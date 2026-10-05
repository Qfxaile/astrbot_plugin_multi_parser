from collections.abc import Mapping

from ..core.parser import BaseParser
from ..core.settings import PluginSettings
from ..platforms.registry import parser_platforms


def build_parsers(config) -> dict[str, BaseParser]:
    """按稳定优先级创建所有平台解析器。"""
    return {
        registration.parser_type.name: registration.parser_type(config)
        for registration in sorted(
            parser_platforms(), key=lambda item: item.parser_priority
        )
    }


def enabled_parsers(config, parsers: Mapping[str, BaseParser]) -> list[BaseParser]:
    """按注册顺序返回当前启用的平台解析器。"""
    defaults = {
        registration.key: registration.enabled_by_default
        for registration in parser_platforms()
    }
    return [
        parser
        for name, parser in parsers.items()
        if PluginSettings(config).platform_enabled(name, defaults.get(name, True))
    ]
