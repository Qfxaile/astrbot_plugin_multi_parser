"""识别抖音链接并分派到对应内容解析器。"""

import re
from urllib.parse import parse_qs, urlparse

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value
from ...core.media import mark_invalid_legacy_images
from ...core.parser import BaseParser
from .common import DouyinContentSupport
from .gallery import DouyinGalleryContent
from .live import DouyinLiveContent
from .music import is_qishui_track_url, parse_qishui_track_html
from .video import DouyinVideoContent
from .work import DouyinWorkContent


class DouyinParser(
    DouyinLiveContent,
    DouyinWorkContent,
    DouyinGalleryContent,
    DouyinVideoContent,
    DouyinContentSupport,
    BaseParser,
):
    """将抖音链接路由到直播、图集、视频或音乐解析器。"""

    name = "douyin"
    display_name = "抖音"
    cookie_config_key = "douyin_cookies"
    image_host_suffixes = (
        "douyinpic.com",
        "byteimg.com",
        "pstatp.com",
        "douyincdn.com",
        "bytedance.com",
    )
    REDIRECT_HOSTS = {"v.douyin.com", "jx.douyin.com", "qishui.douyin.com"}
    PATTERN = (
        r"https?://(?:"
        r"(?:v|jx)\.douyin\.com/[A-Za-z0-9_-]+"
        r"|qishui\.douyin\.com/s/[A-Za-z0-9_-]+[^\s]*"
        r"|live\.douyin\.com/\d+[^\s]*"
        r"|webcast\.amemv\.com/douyin/webcast/reflow/\d+[^\s]*"
        r"|(?:www|m)\.douyin\.com/(?:video|note)/\d+[^\s]*"
        r"|(?:www\.)?iesdouyin\.com/share/(?:slides|video|note)/\d+[^\s]*"
        r"|jingxuan\.douyin\.com/m/(?:slides|video|note)/\d+[^\s]*"
        r"|music\.douyin\.com/qishui/share/track\?[^\s]*track_id=\d+[^\s]*"
        r")"
    )
    IOS_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    }

    async def match(self, context: ParseContext) -> bool:
        return bool(re.search(self.PATTERN, context.combined_text))

    async def parse(self, context: ParseContext) -> ParseResult:
        match = re.search(self.PATTERN, context.combined_text)
        if not match:
            return ParseResult(platform=self.name, error="未找到大陆抖音链接。")

        cookies = build_cookies(
            cookie_config_value(self.config, "douyin_cookies"),
            (".douyin.com", ".iesdouyin.com"),
        )
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=True,
            headers=self.IOS_HEADERS,
            cookies=cookies,
        ) as client:
            url = match.group(0)
            hostname = urlparse(url).hostname or ""
            response = None
            if hostname in self.REDIRECT_HOSTS:
                response = await client.get(url)
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                url = str(response.url)

            if is_qishui_track_url(url):
                if response is None:
                    response = await client.get(url)
                    self.raise_for_response_status(response)
                    self._raise_for_auth_page(response)
                result = parse_qishui_track_html(response.text, platform=self.name)
                mark_invalid_legacy_images(result, self.INVALID_IMAGE_URL)
                return await self.materialize_images(result, client, url)

            if reflow_match := re.search(self.LIVE_REFLOW_PATTERN, url):
                if response is None:
                    response = await client.get(url)
                    self.raise_for_response_status(response)
                    self._raise_for_auth_page(response)
                result = self._parse_live_reflow_html(response.text)
                mark_invalid_legacy_images(result, self.INVALID_IMAGE_URL)
                return await self.materialize_images(
                    result, client, reflow_match.group(0)
                )

            if live_match := re.search(
                r"https?://live\.douyin\.com/(?P<web_rid>\d+)", url
            ):
                return await self._parse_live(client, live_match.group("web_rid"))

            work_match = re.search(
                r"/(?:share/|m/)?(?P<type>slides|video|note)/(?P<id>\d+)", url
            )
            if not work_match:
                query = parse_qs(urlparse(url).query)
                work_id = (query.get("aweme_id") or [""])[0]
                work_type = "video"
            else:
                work_id = work_match.group("id")
                work_type = work_match.group("type")
            if not work_id:
                raise ValueError("无法从抖音链接中提取作品 ID")

            share_url = f"https://www.iesdouyin.com/share/{work_type}/{work_id}/"
            if work_type == "slides":
                response = await client.get(
                    self.SLIDES_URL,
                    params={"aweme_ids": f"[{work_id}]", "request_source": "200"},
                )
                self.raise_for_response_status(response)
                result = self._parse_slides_data(response.json())
            else:
                await self._ensure_ttwid(client)
                response = await client.get(share_url, headers={"Referer": share_url})
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                result = self._parse_router_data(
                    self._extract_router_data(response.text)
                )

            play_token = ""
            retained_lines = []
            for line in result.extra_lines:
                if line.startswith("play_token="):
                    play_token = line.removeprefix("play_token=")
                else:
                    retained_lines.append(line)
            result.extra_lines = retained_lines
            if play_token:
                probed_url = await self._probe_video_url(client, play_token, share_url)
                if probed_url:
                    result.video_url = probed_url

            mark_invalid_legacy_images(result, self.INVALID_IMAGE_URL)
            return await self.materialize_images(result, client, share_url)
