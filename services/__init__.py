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
    "CookieStore",
    "DeliveryPolicy",
    "EventIdentity",
    "OneBotGateway",
    "SummaryProviderResolver",
    "VideoDeliveryService",
    "VideoFallbackService",
    "LoginAttempt",
    "LoginSessionRegistry",
    "LoginStatusService",
    "ServiceContainer",
    "ForwardDeliveryService",
    "OneBotIdentityResolver",
]

_EXPORT_MODULES = {
    "AuthenticationService": ".authentication",
    "DeliveryService": ".delivery",
    "VideoSendPolicy": ".video",
    "VideoSizeInfo": ".video",
    "VideoSizeProbe": ".video",
    "build_parsers": ".configuration",
    "enabled_parsers": ".configuration",
    "CookieStore": ".cookie_store",
    "DeliveryPolicy": ".delivery_policy",
    "EventIdentity": ".event_identity",
    "OneBotGateway": ".onebot_gateway",
    "SummaryProviderResolver": ".summary_provider",
    "VideoDeliveryService": ".video_delivery",
    "VideoFallbackService": ".video_fallback",
    "LoginAttempt": ".login_sessions",
    "LoginSessionRegistry": ".login_sessions",
    "LoginStatusService": ".login_status",
    "ServiceContainer": ".container",
    "ForwardDeliveryService": ".forward_delivery",
    "OneBotIdentityResolver": ".onebot_identity",
}


def __getattr__(name: str):
    """按需加载历史公开导出，保持包入口轻量。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
