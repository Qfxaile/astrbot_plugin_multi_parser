"""插件应用服务。"""

from importlib import import_module

__all__ = [
    "AuthenticationService",
    "DeliveryService",
    "VideoSendPolicy",
    "VideoSizeInfo",
    "VideoSizeProbe",
    "build_parsers",
    "enabled_parsers",
]

_EXPORT_MODULES = {
    "AuthenticationService": ".authentication",
    "DeliveryService": ".delivery",
    "VideoSendPolicy": ".video",
    "VideoSizeInfo": ".video",
    "VideoSizeProbe": ".video",
    "build_parsers": ".configuration",
    "enabled_parsers": ".configuration",
}


def __getattr__(name: str):
    """按需加载历史公开导出，保持包入口轻量。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
