"""提取微信公众号公开文章的标题、作者和有序正文。"""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import httpx

from ...core.contracts import OrderedContent, ParseResult

_BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "section",
    "table",
    "td",
    "tr",
}
_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
_IGNORED_TAGS = {"noscript", "script", "style"}


def _normalize_image_url(value: str) -> str:
    """规范化公众号图片地址，并移除仅供页面定位的片段。"""
    url = value.strip()
    if url.startswith("//"):
        url = f"https:{url}"
    try:
        parsed = urlsplit(url)
        _ = parsed.port
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return urlunsplit(parsed._replace(fragment=""))


class _ArticleHTMLParser(HTMLParser):
    """按微信公众号正文节点顺序提取可见文本和图片。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.contents: list[OrderedContent] = []
        self._meta_title = ""
        self._content_depth = 0
        self._ignored_depth = 0
        self._title_depth = 0
        self._author_depth = 0
        self._title_parts: list[str] = []
        self._author_parts: list[str] = []
        self._text_parts: list[str] = []
        self._seen_images: set[str] = set()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("property") == "og:title":
            self._meta_title = str(attributes.get("content") or "").strip()

        if attributes.get("id") == "activity-name":
            self._title_depth = 1
        elif self._title_depth and tag not in _VOID_TAGS:
            self._title_depth += 1

        if attributes.get("id") == "js_name":
            self._author_depth = 1
        elif self._author_depth and tag not in _VOID_TAGS:
            self._author_depth += 1

        if not self._content_depth and attributes.get("id") == "js_content":
            self._content_depth = 1
            return
        if not self._content_depth:
            return

        if tag in _BLOCK_TAGS or tag == "br":
            self._flush_text()
        if tag in _IGNORED_TAGS:
            self._ignored_depth += 1
        if tag == "img" and not self._ignored_depth:
            self._append_image(attributes)
        if tag not in _VOID_TAGS:
            self._content_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._title_depth:
            self._title_depth -= 1
            if not self._title_depth:
                self.title = "".join(self._title_parts).strip()
        if self._author_depth:
            self._author_depth -= 1
            if not self._author_depth:
                self.author = "".join(self._author_parts).strip()

        if not self._content_depth:
            return
        if tag in _BLOCK_TAGS:
            self._flush_text()
        if tag in _IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        self._content_depth -= 1
        if not self._content_depth:
            self._flush_text()

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._title_depth:
            self._title_parts.append(text)
        if self._author_depth:
            self._author_parts.append(text)
        if self._content_depth and not self._ignored_depth:
            if (
                self._text_parts
                and self._text_parts[-1][-1:].isascii()
                and self._text_parts[-1][-1:].isalnum()
                and text[:1].isascii()
                and text[:1].isalnum()
            ):
                self._text_parts.append(" ")
            self._text_parts.append(text)

    def close(self) -> None:
        super().close()
        self._flush_text()
        self.title = self.title or self._meta_title

    def _append_image(self, attributes: dict[str, str | None]) -> None:
        self._flush_text()
        candidate = str(
            attributes.get("data-src")
            or attributes.get("data-original")
            or attributes.get("src")
            or ""
        )
        image_url = _normalize_image_url(candidate)
        if not image_url or image_url in self._seen_images:
            return
        self._seen_images.add(image_url)
        self.contents.append(OrderedContent(kind="image", value=image_url))

    def _flush_text(self) -> None:
        text = "".join(self._text_parts).strip()
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))


def parse_article_html(html: str) -> ParseResult:
    """将微信公众号文章 HTML 转换为统一解析结果。

    参数:
        html: 微信公众号公开文章页面 HTML。

    返回:
        包含标题、公众号名和有序正文的解析结果。

    异常:
        ValueError: 页面未暴露可读取的公开正文时抛出。
    """
    parser = _ArticleHTMLParser()
    parser.feed(html)
    parser.close()
    if not parser.contents:
        raise ValueError("微信公众号正文不可访问，页面可能已失效或要求安全验证。")
    return ParseResult(
        platform="wechat",
        title=parser.title or "微信公众号文章",
        author=parser.author or "未知公众号",
        ordered_contents=parser.contents,
    )


class WeChatArticleContent:
    """请求并解析微信公众号文章。"""

    async def _parse_article(self, url: str) -> ParseResult:
        headers = self._article_headers(url)
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=headers,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            result = parse_article_html(response.text)
            return await self.materialize_images(result, client, url)

    @staticmethod
    def _article_headers(referer: str) -> dict[str, str]:
        return {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": referer,
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }
