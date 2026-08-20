"""识别并解析 QQ 空间公开说说分享链接。"""

import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser
from .content import IMAGE_HOST_SUFFIXES, VIDEO_HOST_SUFFIXES, QzonePageContent


class QzoneParser(QzonePageContent, BaseParser):
    """解析无需登录即可访问的 QQ 空间公开说说分享页。"""

    name = "qzone"
    display_name = "QQ空间"
    image_host_suffixes = IMAGE_HOST_SUFFIXES
    URL_PATTERN = re.compile(
        r"https://(?:h5|mobile)\.qzone\.qq\.com/[^\s<>\"']+",
        re.IGNORECASE,
    )
    CELL_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
    SHARE_ID_PATTERN = re.compile(r"[A-Fa-f0-9-]{1,128}\Z")
    ENCRYPTED_DATA_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,8192}\Z")
    PAGE_HOST_SUFFIXES = ("qzone.qq.com",)
    MAX_PAGE_BYTES = 2 * 1024 * 1024
    MAX_REDIRECTS = 3
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
            "Chrome/131.0 Mobile Safari/537.36"
        ),
    }
    LOGIN_MARKERS = (
        "该内容仅主人可见",
        "空间设置了访问权限",
        "请使用QQ登录后查看",
    )
    UNAVAILABLE_MARKERS = (
        "该内容已被删除",
        "说说不存在",
        "内容已失效",
    )

    async def match(self, context: ParseContext) -> bool:
        return self._find_share_url(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        url = self._find_share_url(context.combined_text)
        if url is None:
            return ParseResult(platform=self.name, error="未找到QQ空间说说链接。")
        res_uin = self._uin_from_share_url(url)
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
                **self.http_client_options,
            ) as client:
                html_text = await self._request_page(client, url)
                if self._is_universal_url(url):
                    result = self._parse_universal_page(html_text)
                else:
                    result = self._parse_page(
                        html_text,
                        res_uin,
                        title=self._legacy_page_title(url),
                    )
                if result.error:
                    return result
                if result.video_url:
                    result.video_download_headers = {
                        "Referer": url,
                        "User-Agent": self.HEADERS["User-Agent"],
                    }
                    result.video_download_host_suffixes = VIDEO_HOST_SUFFIXES
                return await self.materialize_images(result, client, url)
        except ValueError as exc:
            return ParseResult(platform=self.name, error=str(exc))
        except httpx.HTTPError:
            return ParseResult(
                platform=self.name,
                error="QQ空间说说请求失败，请稍后重试。",
            )

    async def _request_page(self, client: httpx.AsyncClient, url: str) -> str:
        current_url = url
        for redirect_count in range(self.MAX_REDIRECTS + 1):
            async with client.stream("GET", current_url) as response:
                if response.status_code in {401, 403}:
                    raise ValueError("该QQ空间说说需要登录或无权访问。")
                if response.status_code in {404, 410}:
                    raise ValueError("该QQ空间说说已删除或不存在。")
                if 300 <= response.status_code < 400:
                    if redirect_count >= self.MAX_REDIRECTS:
                        raise ValueError("QQ空间分享链接重定向次数超过安全限制。")
                    target_url = urljoin(
                        current_url,
                        response.headers.get("Location", ""),
                    )
                    if not is_trusted_https_url(
                        target_url,
                        self.PAGE_HOST_SUFFIXES,
                    ):
                        raise ValueError("QQ空间分享链接跳转到不可信域名。")
                    current_url = target_url
                    continue

                response.raise_for_status()
                content_length = response.headers.get("Content-Length", "")
                if (
                    content_length.isdigit()
                    and int(content_length) > self.MAX_PAGE_BYTES
                ):
                    raise ValueError("QQ空间页面响应过大，已停止解析。")

                chunks: list[bytes] = []
                total_bytes = 0
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    total_bytes += len(chunk)
                    if total_bytes > self.MAX_PAGE_BYTES:
                        raise ValueError("QQ空间页面响应过大，已停止解析。")
                    chunks.append(chunk)
                html_text = b"".join(chunks).decode("utf-8", errors="replace")
                self._raise_for_page_markers(html_text)
                return html_text
        raise ValueError("QQ空间分享链接重定向次数超过安全限制。")

    def _raise_for_page_markers(self, html_text: str) -> None:
        if any(marker in html_text for marker in self.LOGIN_MARKERS):
            raise ValueError("该QQ空间说说需要登录或无权访问。")
        if any(marker in html_text for marker in self.UNAVAILABLE_MARKERS):
            raise ValueError("该QQ空间说说已删除或不存在。")

    @classmethod
    def _find_share_url(cls, text: str) -> str | None:
        for match in cls.URL_PATTERN.finditer(text):
            url = match.group(0).rstrip(".,!?;:，。！？；：）】》")
            if cls._is_valid_share_url(url):
                return url
        return None

    @classmethod
    def _is_valid_share_url(cls, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        if (
            parsed.scheme != "https"
            or port not in {None, 443}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return False
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.hostname == "h5.qzone.qq.com" and parsed.path == "/ugc/share/":
            return cls._valid_legacy_query(
                query,
                uin_key="res_uin",
                cell_key="cellid",
            )
        if parsed.hostname == "mobile.qzone.qq.com" and parsed.path == "/l":
            return cls._valid_legacy_query(query, uin_key="u", cell_key="i")
        if (
            parsed.hostname == "h5.qzone.qq.com"
            and parsed.path == "/universal-share/share"
        ):
            return cls._valid_universal_query(query)
        return False

    @classmethod
    def _valid_legacy_query(
        cls,
        query: dict[str, list[str]],
        *,
        uin_key: str,
        cell_key: str,
    ) -> bool:
        uins = query.get(uin_key, [])
        cell_ids = query.get(cell_key, [])
        return (
            len(uins) == 1
            and uins[0].isdigit()
            and len(cell_ids) == 1
            and cls.CELL_ID_PATTERN.fullmatch(cell_ids[0]) is not None
        )

    @classmethod
    def _valid_universal_query(cls, query: dict[str, list[str]]) -> bool:
        busi_data = query.get("busi_data", [])
        encrypted_data = query.get("data", [])
        if (
            len(busi_data) != 1
            or len(encrypted_data) != 1
            or cls.ENCRYPTED_DATA_PATTERN.fullmatch(encrypted_data[0]) is None
        ):
            return False
        try:
            metadata = json.loads(busi_data[0])
        except json.JSONDecodeError:
            return False
        share_id = metadata.get("share_id", "") if isinstance(metadata, dict) else ""
        return cls.SHARE_ID_PATTERN.fullmatch(str(share_id)) is not None

    @staticmethod
    def _is_universal_url(url: str) -> bool:
        parsed = urlsplit(url)
        return (
            parsed.hostname == "h5.qzone.qq.com"
            and parsed.path == "/universal-share/share"
        )

    @staticmethod
    def _uin_from_share_url(url: str) -> str:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        key = "u" if parsed.hostname == "mobile.qzone.qq.com" else "res_uin"
        values = query.get(key, [])
        return values[0] if len(values) == 1 and values[0].isdigit() else ""

    @staticmethod
    def _legacy_page_title(url: str) -> str:
        query = parse_qs(urlsplit(url).query)
        app_ids = query.get("a", []) or query.get("appid", [])
        return "QQ空间相册" if app_ids == ["4"] else "QQ空间说说"
