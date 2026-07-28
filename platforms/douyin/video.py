import re
from urllib.parse import parse_qs, urlparse

import httpx

from ...core.contracts import ParseResult


class DouyinVideoContent:
    """解析抖音视频载荷并探测最佳播放地址。"""

    PLAY_RATIOS = ("1080p", "720p", "540p", "360p")

    def _parse_video_item(
        self,
        item: dict,
        title: str,
        author: str,
    ) -> ParseResult:
        """构建分享页中的视频结果。"""
        video = item.get("video")
        if not isinstance(video, dict):
            video = {}
        fallback_url, play_token = self._extract_video_source(video)
        cover_url = self._select_image_url(video.get("cover"))
        extra_lines = [f"play_token={play_token}"] if play_token else []
        if not fallback_url and not play_token:
            extra_lines.append("无法获取视频直链。")
        return ParseResult(
            platform=self.name,
            title=title,
            author=author,
            cover_urls=[cover_url] if cover_url else [],
            video_url=fallback_url,
            extra_lines=extra_lines,
        )

    @staticmethod
    def _extract_video_source(video: object) -> tuple[str, str]:
        """提取视频载荷中的播放地址和清晰度探测令牌。"""
        if not isinstance(video, dict):
            return "", ""
        play_addr = video.get("play_addr")
        if not isinstance(play_addr, dict):
            play_addr = {}
        fallback_urls = play_addr.get("url_list") or []
        if not isinstance(fallback_urls, list):
            fallback_urls = []
        fallback_urls = [url for url in fallback_urls if isinstance(url, str) and url]
        fallback_url = (
            fallback_urls[0].replace("playwm", "play") if fallback_urls else ""
        )
        play_token = str(play_addr.get("uri") or "")
        if not play_token:
            for video_url in fallback_urls:
                query = parse_qs(urlparse(str(video_url)).query)
                if query.get("video_id"):
                    play_token = query["video_id"][0]
                    break
        return fallback_url, play_token

    async def _probe_video_url(
        self,
        client: httpx.AsyncClient,
        video_id: str,
        referer: str,
    ) -> str:
        candidates: list[tuple[int, str]] = []
        for ratio in self.PLAY_RATIOS:
            try:
                async with client.stream(
                    "GET",
                    "https://aweme.snssdk.com/aweme/v1/play/",
                    params={"video_id": video_id, "ratio": ratio},
                    headers={"Range": "bytes=0-1", "Referer": referer},
                ) as response:
                    if response.status_code != 206:
                        continue
                    size = self._extract_response_size(response.headers)
                    if size > 0:
                        candidates.append((size, str(response.url)))
            except httpx.HTTPError:
                continue
        return max(candidates, default=(0, ""), key=lambda item: item[0])[1]

    @staticmethod
    def _extract_response_size(headers: httpx.Headers) -> int:
        if content_range := headers.get("Content-Range"):
            if matched := re.search(r"/(\d+)\s*$", content_range):
                return int(matched.group(1))
        content_length = headers.get("Content-Length", "")
        return int(content_length) if content_length.isdigit() else 0
