from html.parser import HTMLParser
from time import time
from uuid import uuid4

import httpx

from ...core.contracts import OrderedContent, ParseResult
from .common import normalize_url


class _WeiboArticleParser(HTMLParser):
    """按微博长文章中的可见顺序提取文本和图片。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contents: list[OrderedContent] = []
        self._text_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "h1", "h2", "h3", "br"}:
            self._flush_text()
        if tag == "img":
            self._flush_text()
            attributes = dict(attrs)
            image_url = normalize_url(
                attributes.get("data-src") or attributes.get("src")
            )
            if image_url:
                self.contents.append(OrderedContent(kind="image", value=image_url))

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "h1", "h2", "h3"}:
            self._flush_text()

    def handle_data(self, data: str):
        if not self._ignored_depth and (text := data.strip()):
            self._text_parts.append(text)

    def close(self):
        super().close()
        self._flush_text()

    def _flush_text(self):
        text = " ".join(self._text_parts).strip()
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))


class WeiboArticleContent:
    """解析微博长文章。"""

    async def _parse_article(self, article_id: str) -> ParseResult:
        referer = f"https://card.weibo.com/article/m/show/id/{article_id}"
        headers = {**self.HEADERS, "Referer": referer}
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=headers,
            cookies=self._cookies(),
            **self.http_client_options,
        ) as client:
            response = await client.post(
                "https://card.weibo.com/article/m/aj/detail",
                data={"_rid": str(uuid4()), "id": article_id, "_t": int(time() * 1000)},
            )
            self.raise_for_response_status(response)
            payload = response.json()
            self._raise_for_payload_cookie_error(payload)
            result = self._parse_article_payload(payload)
            return await self.materialize_images(result, client, referer)

    @classmethod
    def _parse_article_payload(cls, payload: object) -> ParseResult:
        if not isinstance(payload, dict) or payload.get("msg") != "success":
            raise ValueError("微博长文章请求失败")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("微博长文章数据为空")
        user = data.get("userinfo")
        if not isinstance(user, dict) or not user.get("screen_name"):
            raise ValueError("微博长文章作者数据为空")
        parser = _WeiboArticleParser()
        parser.feed(str(data.get("content") or ""))
        parser.close()
        return ParseResult(
            platform=cls.name,
            title=str(data.get("title") or "微博长文章"),
            author=str(user["screen_name"]),
            ordered_contents=parser.contents,
            extra_lines=[] if parser.contents else ["微博长文章正文为空。"],
        )
