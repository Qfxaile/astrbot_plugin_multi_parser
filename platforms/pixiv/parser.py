"""解析 Pixiv 匿名可访问的公开插画作品。"""

import html
import re
from collections.abc import Mapping
from urllib.parse import parse_qs

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import is_trusted_https_url
from ...core.parser import BaseParser


class PixivParser(BaseParser):
    """通过 Pixiv 官方公开 Ajax 接口解析插画作品。"""

    name = "pixiv"
    display_name = "Pixiv"
    image_host_suffixes = ("pximg.net",)
    ARTWORK_PATTERNS = (
        re.compile(
            r"https?://(?:www\.)?pixiv\.net/artworks/(?P<artwork_id>\d+)"
            r"(?:[/?#][^\s]*)?",
            re.IGNORECASE,
        ),
        re.compile(
            r"https?://(?:www\.)?pixiv\.net/member_illust\.php\?"
            r"(?P<legacy_query>[^\s#]*)",
            re.IGNORECASE,
        ),
    )
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.pixiv.net/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    AJAX_BASE_URL = "https://www.pixiv.net/ajax/illust"

    async def match(self, context: ParseContext) -> bool:
        return self._find_artwork_id(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        artwork_id = self._find_artwork_id(context.combined_text)
        if artwork_id is None:
            return ParseResult(platform=self.name, error="未找到 Pixiv 作品链接。")

        artwork_url = f"https://www.pixiv.net/artworks/{artwork_id}"
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
            ) as client:
                metadata = await self._request_body(
                    client, f"{self.AJAX_BASE_URL}/{artwork_id}"
                )
                pages = await self._request_body(
                    client, f"{self.AJAX_BASE_URL}/{artwork_id}/pages"
                )
                result = self._parse_illust_payload(metadata, pages)
                return await self.materialize_images(result, client, artwork_url)
        except ValueError as exc:
            return ParseResult(platform=self.name, error=str(exc))
        except (httpx.HTTPError, TypeError, KeyError):
            return ParseResult(
                platform=self.name,
                error="Pixiv作品请求失败，请稍后重试。",
            )

    async def _request_body(self, client: httpx.AsyncClient, url: str) -> object:
        response = await client.get(url)
        self.raise_for_response_status(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ValueError("Pixiv返回了无法读取的作品数据。") from exc
        if not isinstance(payload, Mapping) or payload.get("error"):
            raise ValueError("Pixiv作品不可访问，可能已删除或受到访问限制。")
        body = payload.get("body")
        if body is None:
            raise ValueError("Pixiv作品不可访问，可能已删除或受到访问限制。")
        return body

    def _parse_illust_payload(
        self,
        metadata: object,
        pages: object,
    ) -> ParseResult:
        if not isinstance(metadata, Mapping):
            raise ValueError("Pixiv作品不可访问，可能已删除或受到访问限制。")

        illust_type = metadata.get("illustType")
        if illust_type == 2:
            raise ValueError("Pixiv 动图暂不支持解析。")
        if illust_type == 3:
            raise ValueError("Pixiv 小说暂不支持解析。")

        image_urls = self._page_image_urls(metadata, pages)
        if not image_urls:
            raise ValueError("Pixiv作品中未找到可发送的公开图片。")

        tags = self._tags(metadata.get("tags"))
        return ParseResult(
            platform=self.name,
            title=self._clean_text(metadata.get("title")) or "Pixiv作品",
            author=self._clean_text(metadata.get("userName")) or "未知作者",
            description=self._clean_text(metadata.get("description")),
            image_urls=image_urls,
            extra_lines=[f"标签：{'、'.join(tags)}"] if tags else [],
            image_download_headers={
                "Referer": "https://www.pixiv.net/",
                "User-Agent": self.HEADERS["User-Agent"],
            },
        )

    def _page_image_urls(self, metadata: Mapping, pages: object) -> list[str]:
        candidates: list[object] = []
        if isinstance(pages, list):
            candidates.extend(pages)
        if not candidates:
            candidates.append(metadata)

        image_urls: list[str] = []
        seen: set[str] = set()
        for page in candidates:
            if not isinstance(page, Mapping):
                continue
            urls = page.get("urls")
            original = urls.get("original") if isinstance(urls, Mapping) else None
            if not isinstance(original, str) or not self._is_trusted_image_url(
                original
            ):
                continue
            if original not in seen:
                seen.add(original)
                image_urls.append(original)
        return image_urls

    @staticmethod
    def _tags(value: object) -> list[str]:
        if not isinstance(value, Mapping) or not isinstance(value.get("tags"), list):
            return []
        return [
            tag
            for item in value["tags"]
            if isinstance(item, Mapping)
            and isinstance(tag := item.get("tag"), str)
            and (tag := PixivParser._clean_text(tag))
        ]

    @staticmethod
    def _clean_text(value: object) -> str:
        text = html.unescape(str(value or ""))
        text = re.sub(r"<[^>]+>", " ", text)
        return " ".join(text.split())

    @classmethod
    def _is_trusted_image_url(cls, url: str) -> bool:
        return is_trusted_https_url(url, cls.image_host_suffixes)

    @classmethod
    def _find_artwork_id(cls, text: str) -> str | None:
        for pattern in cls.ARTWORK_PATTERNS:
            match = pattern.search(text)
            if match:
                artwork_id = match.groupdict().get("artwork_id")
                if artwork_id:
                    return artwork_id
                query = match.groupdict().get("legacy_query") or ""
                values = parse_qs(query).get("illust_id")
                if values and values[0].isdigit():
                    return values[0]
        return None
