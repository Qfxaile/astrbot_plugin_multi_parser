"""识别小红书链接并获取图集或视频笔记。"""

import json
import re
from urllib.parse import urlparse, urlsplit, urlunsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value
from ...core.media import mark_invalid_legacy_images
from ...core.parser import BaseParser
from .gallery import RedBookGalleryContent
from .note import RedBookNoteContent
from .video import RedBookVideoContent


class RedBookParser(
    RedBookNoteContent,
    RedBookGalleryContent,
    RedBookVideoContent,
    BaseParser,
):
    """路由小红书笔记并维持受控的页面回退流程。"""

    name = "redbook"
    display_name = "小红书"
    cookie_config_key = "redbook_cookies"
    image_host_suffixes = ("xhscdn.com", "xiaohongshu.com")
    INVALID_IMAGE_URL = "unsafe-image-url"
    PATTERN = (
        r"https?://(?:"
        r"www\.xiaohongshu\.com/(?:explore|discovery/item)/[^/?\s]+"
        r"|xhslink\.com(?:/[^/?\s]+)+"
        r")(?:\?[^\s#]*)?"
    )
    NOTE_PATH_PATTERN = r"/(?:explore|discovery/item)/(?P<note_id>[^/?]+)"
    AUTH_PATH_MARKERS = ("/404/security-check", "/login", "/website-login")
    EXPLORE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/55.0.2883.87 "
            "UBrowser/6.2.4098.3 Safari/537.36"
        )
    }
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Origin": "https://www.xiaohongshu.com",
    }

    async def match(self, context: ParseContext) -> bool:
        return bool(re.search(self.PATTERN, context.combined_text))

    async def parse(self, context: ParseContext) -> ParseResult:
        match = re.search(self.PATTERN, context.combined_text)
        if not match:
            return ParseResult(platform=self.name, error="未找到小红书链接。")

        cookies = build_cookies(
            cookie_config_value(self.config, "redbook_cookies"),
            (".xiaohongshu.com",),
        )
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=True,
            headers=self.HEADERS,
            cookies=cookies,
            **self.http_client_options,
        ) as client:
            url = match.group(0)
            if (urlparse(url).hostname or "") == "xhslink.com":
                response = await client.get(url, follow_redirects=False)
                if not response.has_redirect_location:
                    self.raise_for_response_status(response)
                    raise ValueError("小红书短链未返回重定向地址")
                url = str(response.url.join(response.headers["Location"]))
                if self._is_auth_url(url):
                    raise self.cookie_access_error()
                if (urlparse(url).hostname or "") != "www.xiaohongshu.com":
                    raise ValueError("小红书短链重定向到不受支持的地址")

            parsed_url = urlparse(url)
            note_match = re.search(self.NOTE_PATH_PATTERN, parsed_url.path)
            if not note_match:
                raise ValueError("无法从小红书链接中提取笔记 ID")
            note_id = note_match.group("note_id")
            query = f"?{parsed_url.query}" if parsed_url.query else ""
            explore_url = f"https://www.xiaohongshu.com/explore/{note_id}{query}"
            discovery_url = (
                f"https://www.xiaohongshu.com/discovery/item/{note_id}{query}"
            )

            try:
                original_headers = client.headers.copy()
                client.headers.clear()
                client.headers.update(self.EXPLORE_HEADERS)
                try:
                    response = await client.get(explore_url)
                finally:
                    client.headers.clear()
                    client.headers.update(original_headers)
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                result = self._parse_explore_state(
                    self._extract_initial_state(response.text), note_id
                )
                content_url = explore_url
            except (httpx.HTTPError, ValueError, KeyError):
                response = await client.get(discovery_url)
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                result = self._parse_discovery_state(
                    self._extract_initial_state(response.text)
                )
                content_url = discovery_url
            parsed_content_url = urlsplit(content_url)
            image_referer = urlunsplit(
                parsed_content_url._replace(query="", fragment="")
            )
            mark_invalid_legacy_images(result, self.INVALID_IMAGE_URL)
            client.cookies.clear()
            return await self.materialize_images(result, client, image_referer)

    def _raise_for_auth_page(self, response: httpx.Response) -> None:
        """识别小红书跳转后的安全验证或登录页面。"""
        if self._is_auth_url(str(response.url)):
            raise self.cookie_access_error()

    @classmethod
    def _is_auth_url(cls, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in cls.AUTH_PATH_MARKERS)

    @staticmethod
    def _extract_initial_state(html: str) -> dict:
        matched = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
            html,
            flags=re.DOTALL,
        )
        if not matched:
            raise ValueError("小红书分享链接失效或内容已删除")
        return json.loads(matched.group(1).strip().replace("undefined", "null"))
