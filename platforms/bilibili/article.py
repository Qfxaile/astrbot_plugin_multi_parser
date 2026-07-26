from html.parser import HTMLParser

import httpx

from ...core.contracts import OrderedContent, ParseResult
from .common import original_image_url


class _ArticleHTMLParser(HTMLParser):
    """按文档顺序提取专栏中可见的文本和图片。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.contents: list[OrderedContent] = []
        self._article_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attributes = dict(attrs)
        if tag == "meta":
            if attributes.get("property") == "og:title":
                self.title = str(attributes.get("content") or "")
            elif attributes.get("name") == "author":
                self.author = str(attributes.get("content") or "")

        classes = str(attributes.get("class") or "").split()
        if (
            not self._article_depth
            and tag == "div"
            and {"article-holder", "article-content"}.intersection(classes)
        ):
            self._article_depth = 1
            return
        if not self._article_depth:
            return
        if tag == "div":
            self._article_depth += 1
        if tag in {"p", "h1", "h2", "h3", "li", "blockquote", "br"}:
            self._flush_text()
        if tag == "img":
            self._flush_text()
            image_url = str(attributes.get("data-src") or attributes.get("src") or "")
            image_url = original_image_url(image_url)
            if image_url.startswith(("http://", "https://")):
                self.contents.append(OrderedContent(kind="image", value=image_url))

    def handle_endtag(self, tag: str):
        if not self._article_depth:
            return
        if tag in {"p", "h1", "h2", "h3", "li", "blockquote", "figure"}:
            self._flush_text()
        if tag == "div":
            self._article_depth -= 1
            if not self._article_depth:
                self._flush_text()

    def handle_data(self, data: str):
        if self._article_depth and (text := data.strip()):
            self._text_parts.append(text)

    def _flush_text(self):
        text = "".join(self._text_parts).strip()
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))


class BilibiliArticleContent:
    """解析 B站专栏文章。"""

    ARTICLE_API = "https://api.bilibili.com/x/article/view"

    def _parse_article_payload(self, payload: dict) -> ParseResult:
        """将传统专栏接口载荷转换为包含完整正文的解析结果。"""
        self._raise_for_api_cookie_error(payload)
        if payload.get("code") not in (None, 0):
            raise ValueError(str(payload.get("message") or "B站专栏请求失败"))
        data = payload.get("data") or {}
        content = str(data.get("content") or "")
        if not content:
            raise ValueError("B站专栏正文不可访问")

        parsed = self._parse_article_html(
            f'<div class="article-content">{content}</div>'
        )
        author = data.get("author") or {}
        cover_candidates = data.get("origin_image_urls") or data.get("image_urls") or []
        if not isinstance(cover_candidates, list):
            cover_candidates = []
        cover_urls = []
        for cover in cover_candidates:
            cover_url = original_image_url(str(cover or ""))
            if (
                cover_url.startswith(("http://", "https://"))
                and cover_url not in cover_urls
            ):
                cover_urls.append(cover_url)
        ordered_contents = [
            OrderedContent(kind="image", value=cover_url) for cover_url in cover_urls
        ]
        ordered_contents.extend(parsed.ordered_contents)
        return ParseResult(
            platform=self.name,
            title=str(data.get("title") or parsed.title),
            author=str(author.get("name") or parsed.author),
            ordered_contents=ordered_contents,
        )

    async def _parse_article(self, article_id: str) -> ParseResult:
        url = f"https://www.bilibili.com/read/cv{article_id}"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(url),
            cookies=self._cookies(),
        ) as client:
            response = await client.get(self.ARTICLE_API, params={"id": article_id})
            self.raise_for_response_status(response)
            result = self._parse_article_payload(response.json())
            return await self.materialize_images(result, client, url)

    def _parse_article_html(self, html: str) -> ParseResult:
        """在不执行页面脚本的情况下提取公开专栏 HTML。"""
        parser = _ArticleHTMLParser()
        parser.feed(html)
        parser.close()
        if not parser.contents:
            raise ValueError("B站专栏正文不可访问")
        return ParseResult(
            platform=self.name,
            title=parser.title or "B站专栏",
            author=parser.author or "未知作者",
            ordered_contents=parser.contents,
        )
