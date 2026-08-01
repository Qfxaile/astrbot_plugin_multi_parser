"""解析番茄小说公开分享页。"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser
from ...core.webpage import TrustedWebPageError, fetch_trusted_html


@dataclass(frozen=True)
class _NovelMetadata:
    title: str = ""
    author: str = ""
    description: str = ""
    cover_url: str = ""

    def with_fallback(self, fallback: "_NovelMetadata") -> "_NovelMetadata":
        return _NovelMetadata(
            title=self.title or fallback.title,
            author=self.author or fallback.author,
            description=self.description or fallback.description,
            cover_url=self.cover_url or fallback.cover_url,
        )


class _SharePageParser(HTMLParser):
    """收集分享页元数据和可能包含作品数据的 JSON 脚本。"""

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.json_scripts: list[str] = []
        self._script_type = ""
        self._script_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = unescape(content)
        elif tag.lower() == "script":
            self._script_type = attributes.get("type", "").lower()
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_type:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._script_type:
            return
        if "json" in self._script_type:
            script = "".join(self._script_chunks).strip()
            if script:
                self.json_scripts.append(script)
        self._script_type = ""
        self._script_chunks = []


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
            return ParseResult(platform=self.name, error="未找到番茄小说分享链接。")

        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
            ) as client:
                book_id = await self._resolve_book_id(client, url)
                if not book_id:
                    return ParseResult(
                        platform=self.name,
                        error="番茄小说分享链接未指向受支持的作品。",
                    )

                detail_url = f"https://fanqienovel.com/page/{book_id}"
                detail_page = await fetch_trusted_html(
                    client,
                    detail_url,
                    self.page_host_suffixes,
                )
                if not self._is_detail_page(detail_page.final_url, book_id):
                    return ParseResult(
                        platform=self.name,
                        error="番茄小说详情页跳转到了不受支持的地址。",
                    )

                metadata = self._extract_metadata(
                    detail_page.html,
                    detail_page.final_url,
                    expected_book_id=book_id,
                )
                if not metadata.title:
                    return ParseResult(
                        platform=self.name,
                        error="番茄小说分享页中未找到可解析的作品信息。",
                    )

                cover_url = metadata.cover_url
                if cover_url and not is_trusted_https_url(
                    cover_url,
                    self.image_host_suffixes,
                ):
                    cover_url = ""
                result = ParseResult(
                    platform=self.name,
                    title=metadata.title,
                    author=metadata.author,
                    description=metadata.description,
                    cover_urls=[cover_url] if cover_url else [],
                )
                if not result.cover_urls:
                    return result
                return await self.materialize_public_images(
                    result,
                    detail_page.final_url,
                    headers=self.HEADERS,
                )
        except TrustedWebPageError as exc:
            return ParseResult(platform=self.name, error=str(exc))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {404, 410}:
                return ParseResult(
                    platform=self.name,
                    error="该番茄小说作品已下架或分享链接已失效。",
                )
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
        """仅检查可信跳转响应头，避免请求并记录带敏感参数的动态分享页。"""
        current_url = url
        for _ in range(6):
            if book_id := self._extract_book_id(current_url):
                return book_id
            if not is_trusted_https_url(current_url, self.page_host_suffixes):
                raise TrustedWebPageError("番茄小说分享链接跳转到不可信域名。")

            response = await client.get(current_url, follow_redirects=False)
            if not response.is_redirect:
                response.raise_for_status()
                return self._extract_book_id(str(response.url))

            location = response.headers.get("Location")
            if not location:
                raise TrustedWebPageError("番茄小说分享链接缺少跳转地址。")
            current_url = urljoin(current_url, location)

        raise TrustedWebPageError("番茄小说分享链接重定向次数超过安全限制。")

    @classmethod
    def _extract_metadata(
        cls,
        html_text: str,
        base_url: str,
        *,
        expected_book_id: str = "",
    ) -> _NovelMetadata:
        parser = _SharePageParser()
        parser.feed(html_text)
        metadata = _NovelMetadata()
        for payload in cls._iter_payloads(html_text, parser.json_scripts):
            for mapping in cls._iter_mappings(payload):
                if expected_book_id and not cls._mapping_matches_book(
                    mapping,
                    expected_book_id,
                ):
                    continue
                candidate = cls._metadata_from_mapping(mapping, base_url)
                if candidate.title:
                    metadata = candidate.with_fallback(metadata)
                    if all(
                        (
                            metadata.title,
                            metadata.author,
                            metadata.description,
                            metadata.cover_url,
                        )
                    ):
                        break
        return metadata.with_fallback(cls._metadata_from_meta(parser.meta, base_url))

    @classmethod
    def _mapping_matches_book(
        cls,
        mapping: Mapping[str, object],
        expected_book_id: str,
    ) -> bool:
        value = mapping.get("bookId") or mapping.get("book_id")
        return isinstance(value, (str, int)) and str(value) == expected_book_id

    @classmethod
    def _iter_payloads(
        cls,
        html_text: str,
        json_scripts: Iterable[str],
    ) -> Iterable[object]:
        for script in json_scripts:
            try:
                yield json.loads(script)
            except (json.JSONDecodeError, RecursionError):
                continue
        for match in cls.ASSIGNED_JSON_PATTERN.finditer(html_text):
            try:
                payload, _ = json.JSONDecoder().raw_decode(html_text, match.end())
            except (json.JSONDecodeError, RecursionError):
                continue
            yield payload

    @classmethod
    def _iter_mappings(
        cls,
        value: object,
        depth: int = 0,
    ) -> Iterable[Mapping[str, object]]:
        if depth > 20:
            return
        if isinstance(value, Mapping):
            yield value
            for child in value.values():
                yield from cls._iter_mappings(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                yield from cls._iter_mappings(child, depth + 1)

    @classmethod
    def _metadata_from_mapping(
        cls,
        mapping: Mapping[str, object],
        base_url: str,
    ) -> _NovelMetadata:
        title = cls._first_text(mapping, cls.TITLE_KEYS)
        if not title:
            return _NovelMetadata()
        return _NovelMetadata(
            title=title,
            author=cls._first_text(mapping, cls.AUTHOR_KEYS),
            description=cls._first_text(mapping, cls.DESCRIPTION_KEYS),
            cover_url=cls._absolute_url(
                cls._first_text(mapping, cls.COVER_KEYS),
                base_url,
            ),
        )

    @classmethod
    def _metadata_from_meta(
        cls,
        meta: Mapping[str, str],
        base_url: str,
    ) -> _NovelMetadata:
        title = meta.get("og:title", "") or meta.get("twitter:title", "")
        description = meta.get("og:description", "") or meta.get("description", "")
        return _NovelMetadata(
            title=cls._clean_text(title),
            description=cls._clean_text(description),
            cover_url=cls._absolute_url(
                meta.get("og:image", "") or meta.get("twitter:image", ""),
                base_url,
            ),
        )

    @classmethod
    def _first_text(
        cls,
        mapping: Mapping[str, object],
        keys: Iterable[str],
    ) -> str:
        for key in keys:
            value = mapping.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                text = cls._clean_text(str(value))
                if text:
                    return text
        return ""

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(unescape(value).split())

    @staticmethod
    def _absolute_url(value: str, base_url: str) -> str:
        if not value:
            return ""
        return urljoin(base_url, value.replace("\\u002F", "/"))

    def _network_error(self) -> ParseResult:
        return ParseResult(
            platform=self.name,
            error="番茄小说分享页请求失败，请稍后重试。",
        )
