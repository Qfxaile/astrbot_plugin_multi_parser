"""识别抖音链接并分派到对应内容解析器。"""

import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value, is_trusted_https_url
from ...core.media import mark_invalid_legacy_images
from ...core.parser import BaseParser
from .common import DouyinContentSupport
from .gallery import DouyinGalleryContent
from .live import DouyinLiveContent
from .music import is_qishui_track_url, parse_qishui_track_html
from .shop import DouyinShopContent
from .video import DouyinVideoContent
from .work import DouyinWorkContent


class DouyinRedirectError(ValueError):
    """表示抖音分享短链未通过受信任跳转校验。"""


class DouyinParser(
    DouyinLiveContent,
    DouyinWorkContent,
    DouyinGalleryContent,
    DouyinVideoContent,
    DouyinShopContent,
    DouyinContentSupport,
    BaseParser,
):
    """将抖音链接路由到直播、图集、视频、音乐或商城解析器。"""

    name = "douyin"
    display_name = "抖音"
    cookie_config_key = "douyin_cookies"
    image_host_suffixes = (
        "douyinpic.com",
        "byteimg.com",
        "pstatp.com",
        "douyincdn.com",
        "bytedance.com",
        "ecombdimg.com",
    )
    REDIRECT_HOSTS = {"v.douyin.com", "jx.douyin.com", "qishui.douyin.com"}
    REDIRECT_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com", "amemv.com")
    MAX_REDIRECTS = 5
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
        r"|haohuo\.jinritemai\.com/ecommerce/trade/detail/index\.html\?[^\s]+"
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
            **self.http_client_options,
        ) as client:
            url = match.group(0)
            hostname = urlparse(url).hostname or ""
            response = None
            if hostname in self.REDIRECT_HOSTS:
                try:
                    response = await self._resolve_short_link(client, url)
                except DouyinRedirectError as exc:
                    return ParseResult(platform=self.name, error=str(exc))
                url = str(response.url)

            if self._is_shop_url(url):
                result = self._parse_shop_url(url)
                if result.error or not result.cover_urls:
                    return result
                return await self.materialize_public_images(
                    result,
                    url,
                    headers=self.IOS_HEADERS,
                )

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

    async def _resolve_short_link(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> httpx.Response:
        """在抖音可信域内逐跳解析分享短链。"""
        current_url = url
        redirect_count = 0
        while True:
            response = await client.get(current_url, follow_redirects=False)
            if not response.is_redirect:
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                return response

            redirect_count += 1
            if redirect_count > self.MAX_REDIRECTS:
                raise DouyinRedirectError("抖音分享链接重定向次数超过安全限制。")

            location = response.headers.get("Location")
            if not location:
                raise DouyinRedirectError("抖音分享链接缺少跳转地址。")
            target_url = urljoin(current_url, location)
            if not self._is_trusted_redirect_url(target_url):
                raise DouyinRedirectError("抖音分享链接跳转到不可信域名。")
            current_url = target_url

    @classmethod
    def _is_trusted_redirect_url(cls, url: str) -> bool:
        return is_trusted_https_url(
            url,
            cls.REDIRECT_HOST_SUFFIXES,
        ) or cls._is_shop_url(url)
