"""消息投递应用服务和协议适配。"""

from .forward import ForwardDeliveryService
from .policy import DeliveryPolicy
from .service import DeliveryService
from .video import VideoSendPolicy, VideoSizeInfo, VideoSizeProbe
from .video_delivery import VideoDeliveryService
from .video_fallback import VideoFallbackService

__all__ = [
    "DeliveryPolicy",
    "DeliveryService",
    "ForwardDeliveryService",
    "VideoDeliveryService",
    "VideoFallbackService",
    "VideoSendPolicy",
    "VideoSizeInfo",
    "VideoSizeProbe",
]
