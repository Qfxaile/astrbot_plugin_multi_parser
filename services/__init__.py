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
    "AuthenticationService": ".authentication.service",
    "DeliveryService": ".delivery.service",
    "VideoSendPolicy": ".delivery.video",
    "VideoSizeInfo": ".delivery.video",
    "VideoSizeProbe": ".delivery.video",
    "build_parsers": ".composition.configuration",
    "enabled_parsers": ".composition.configuration",
    "CookieStore": ".authentication.cookie_store",
    "DeliveryPolicy": ".delivery.policy",
    "EventIdentity": ".event_identity",
    "OneBotGateway": ".delivery.onebot_gateway",
    "SummaryProviderResolver": ".summary.provider",
    "VideoDeliveryService": ".delivery.video_delivery",
    "VideoFallbackService": ".delivery.video_fallback",
    "LoginAttempt": ".authentication.sessions",
    "LoginSessionRegistry": ".authentication.sessions",
    "LoginStatusService": ".authentication.status",
    "ServiceContainer": ".composition.container",
    "ForwardDeliveryService": ".delivery.forward",
    "OneBotIdentityResolver": ".delivery.onebot_identity",
}


def __getattr__(name: str):
    """按需加载历史公开导出，保持包入口轻量。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
