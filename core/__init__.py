"""插件核心契约与跨平台基础能力。"""

from importlib import import_module

__all__ = ["BaseParser", "OrderedContent", "ParseContext", "ParseResult"]

_EXPORT_MODULES = {
    "BaseParser": ".parser",
    "OrderedContent": ".contracts",
    "ParseContext": ".contracts",
    "ParseResult": ".contracts",
}


def __getattr__(name: str):
    """按需加载历史公开导出，避免导入包时加载具体实现。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
