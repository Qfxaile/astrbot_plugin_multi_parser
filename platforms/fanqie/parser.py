"""解析番茄小说公开分享页。"""

import re
from urllib.parse import parse_qs, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser
from ...core.webpage import TrustedWebPageError, fetch_trusted_html
from .client import resolve_book_id
from .content import extract_metadata


class FanqieParser(BaseParser):
    """解析番茄小说公开分享链接中的作品信息。"""

    name = "fanqie"
    display_name = "番茄小说"
    page_host_suffixes = ("changdunovel.com", "fanqienovel.com")
    image_host_suffixes = ("byteimg.com", "fanqienovel.com", "changdunovel.com")
    URL_PATTERN = re.compile(
        r"https://[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    SHARE_PATH_PATTERN = re.compile(r"/t/[A-Za-z0-9_-]{1,128}/?\Z")
    DETAIL_PATH_PATTERN = re.compile(r"/page/(?P<book_id>\d{1,32})/?\Z")
    BOOK_ID_PATTERN = re.compile(r"\d{1,32}\Z")
    ASSIGNED_JSON_PATTERN = re.compile(
        r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|__NUXT__|_ROUTER_DATA)\s*=\s*"
    )
    TITLE_KEYS = ("book_name", "bookName", "title", "name")
    AUTHOR_KEYS = ("author", "author_name", "authorName")
    DESCRIPTION_KEYS = (
        "abstract",
        "description",
        "book_abstract",
        "bookAbstract",
        "intro",
    )
    COVER_KEYS = (
        "thumb_url",
        "thumbUrl",
        "thumbUri",
        "cover_url",
        "coverUrl",
        "book_cover",
        "bookCover",
        "image",
    )
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
            "Chrome/131.0 Mobile Safari/537.36"
        ),
    }

    async def match(self, context: ParseContext) -> bool:
        return self._find_share_url(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        url = self._find_share_url(context.combined_text)
        if url is None:
            return self._error_result("未找到番茄小说分享链接。")

        try:
            async with self.http_client(headers=self.HEADERS) as client:
                book_id = await self._resolve_book_id(client, url)
                if not book_id:
                    return self._error_result("番茄小说分享链接未指向受支持的作品。")

                detail_url = f"https://fanqienovel.com/page/{book_id}"
                detail_page = await fetch_trusted_html(
                    client,
                    detail_url,
                    self.page_host_suffixes,
                )
                if not self._is_detail_page(detail_page.final_url, book_id):
                    return self._error_result("番茄小说详情页跳转到了不受支持的地址。")

                metadata = self._extract_metadata(
                    detail_page.html,
                    detail_page.final_url,
                    expected_book_id=book_id,
                )
                if not metadata.title:
                    return self._error_result(
                        "番茄小说分享页中未找到可解析的作品信息。"
                    )

                cover_url = metadata.cover_url
                if cover_url and not is_trusted_https_url(
                    cover_url,
                    self.image_host_suffixes,
                ):
                    cover_url = ""
                result = ParseResult(platform=self.name)
                result.content.title = metadata.title
                result.content.author = metadata.author
                result.content.description = metadata.description
                if cover_url:
                    result.content.cover_urls.append(cover_url)
                if not result.content.cover_urls:
                    return result
                return await self.materialize_public_images(
                    result,
                    detail_page.final_url,
                    headers=self.HEADERS,
                )
        except TrustedWebPageError as exc:
            return self._error_result(str(exc))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                return self._error_result("该番茄小说作品已下架或分享链接已失效。")
            return self._network_error()
        except httpx.HTTPError:
            return self._network_error()

    @classmethod
    def _find_share_url(cls, text: str) -> str | None:
        for match in cls.URL_PATTERN.finditer(text):
            url = match.group(0).rstrip(".,!?;:，。！？；：）】》")
            if cls._is_supported_url(url):
                return url
        return None

    @classmethod
    def _is_supported_url(cls, url: str) -> bool:
        if not is_trusted_https_url(url, ("changdunovel.com",)):
            return False
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return bool(cls.SHARE_PATH_PATTERN.fullmatch(parsed.path))

    @classmethod
    def _extract_book_id(cls, url: str) -> str:
        """从可信分享跳转或公开详情页中提取书籍 ID。"""
        try:
            parsed = urlsplit(url)
        except ValueError:
            return ""
        host = (parsed.hostname or "").lower()
        if host == "fanqienovel.com" or host.endswith(".fanqienovel.com"):
            match = cls.DETAIL_PATH_PATTERN.fullmatch(parsed.path)
            return match.group("book_id") if match else ""
        if host != "changdunovel.com" and not host.endswith(".changdunovel.com"):
            return ""
        book_ids = parse_qs(parsed.query, keep_blank_values=True).get("book_id", [])
        if len(book_ids) != 1 or cls.BOOK_ID_PATTERN.fullmatch(book_ids[0]) is None:
            return ""
        return book_ids[0]

    @classmethod
    def _is_detail_page(cls, url: str, expected_book_id: str) -> bool:
        return cls._extract_book_id(url) == expected_book_id

    async def _resolve_book_id(
        self,
        client: httpx.AsyncClient,
        url: str,
    ) -> str:
        return await resolve_book_id(
            client,
            url,
            page_host_suffixes=self.page_host_suffixes,
            extract_book_id=self._extract_book_id,
        )

    @classmethod
    def _extract_metadata(
        cls,
        html_text: str,
        base_url: str,
        *,
        expected_book_id: str = "",
    ):
        return extract_metadata(
            html_text,
            base_url,
            expected_book_id=expected_book_id,
        )

    def _network_error(self) -> ParseResult:
        return self._error_result("番茄小说分享页请求失败，请稍后重试。")

    def _error_result(self, message: str) -> ParseResult:
        result = ParseResult(platform=self.name)
        result.diagnostics.error = message
        return result
