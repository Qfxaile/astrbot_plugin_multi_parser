"""AstrBot 消息组件渲染。"""

from .result_renderer import (
    ParseResultRenderer,
    render_audio_chain,
    render_info_chain,
    render_video_chain,
)

__all__ = [
    "ParseResultRenderer",
    "render_audio_chain",
    "render_info_chain",
    "render_video_chain",
]
