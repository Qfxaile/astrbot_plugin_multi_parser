import re
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from astrbot.api import logger

from ..core.http import http_client_proxy_options
from ..core.media import sanitize_media_headers


@dataclass
class VideoSizeInfo:
    size_bytes: int | None = None
    reason: str = ""

    @property
    def size_mb(self) -> float | None:
        if self.size_bytes is None:
            return None
        return self.size_bytes / 1024 / 1024


class VideoSizeProbe:
    """通过 HEAD 或单字节 Range 请求探测视频大小。"""

    def __init__(
        self,
        config: Mapping[str, object],
        platform_name: str = "",
    ) -> None:
        self.config = config
        self.platform_name = platform_name

    async def probe(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
    ) -> VideoSizeInfo:
        timeout = float(
            self.config.get(
                "size_check_timeout_seconds",
                self.config.get("request_timeout_seconds", 30),
            )
        )
        request_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ),
            "Accept": "*/*",
        }
        request_headers.update(sanitize_media_headers(headers))
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=request_headers,
            **http_client_proxy_options(self.config, self.platform_name),
        ) as client:
            try:
                response = await client.head(url)
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                if length and length.isdigit():
                    return VideoSizeInfo(size_bytes=int(length))
            except Exception as exc:
                logger.info(
                    f"HEAD 检查视频大小失败，尝试 Range 请求: {self._error_detail(exc)}"
                )

            try:
                async with client.stream(
                    "GET", url, headers={"Range": "bytes=0-0"}
                ) as response:
                    response.raise_for_status()
                    content_range = response.headers.get("Content-Range", "")
                    size = parse_content_range(content_range)
                    if size is not None:
                        return VideoSizeInfo(size_bytes=size)
                    length = response.headers.get("Content-Length")
                    if length and length.isdigit():
                        length_bytes = int(length)
                        if length_bytes > 1:
                            return VideoSizeInfo(size_bytes=length_bytes)
                        return VideoSizeInfo(reason="服务端未返回完整文件大小")
            except Exception as exc:
                return VideoSizeInfo(
                    reason=f"视频大小检查失败: {self._error_detail(exc)}"
                )

        return VideoSizeInfo(reason="服务端未返回视频大小")

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        return type(exc).__name__


class VideoSendPolicy:
    """根据配置与探测结果决定是否直接发送视频。"""

    def __init__(self, config: Mapping[str, object]) -> None:
        self.config = config

    def decide(self, size_info: VideoSizeInfo) -> tuple[bool, str]:
        max_size_mb = float(self.config.get("max_video_size_mb", 50))
        if max_size_mb <= 0:
            return True, "未启用大小限制"

        if size_info.size_mb is None:
            if bool(self.config.get("allow_unknown_video_size", False)):
                return True, "视频大小未知，已按配置允许发送"
            reason = size_info.reason or "视频大小未知"
            return False, f"{reason}，未直接发送视频"

        if size_info.size_mb > max_size_mb:
            return False, (
                f"视频大小 {format_video_size(size_info.size_mb)} "
                f"超过限制 {max_size_mb:.2f} MB，未直接发送视频"
            )

        return True, (f"视频大小 {format_video_size(size_info.size_mb)}，未超过限制")


def format_video_size(size_mb: float | None) -> str:
    return "未知" if size_mb is None else f"{size_mb:.2f} MB"


def parse_content_range(value: str) -> int | None:
    match = re.search(r"/(\d+)\s*$", value)
    return int(match.group(1)) if match else None
