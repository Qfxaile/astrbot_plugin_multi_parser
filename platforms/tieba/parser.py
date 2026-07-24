from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from ...core.contracts import OrderedContent, ParseContext, ParseResult
from ...core.http import cookie_config_value, parse_cookie_header
from ...core.parser import BaseParser


class _TiebaPageParser(HTMLParser):
    """提取贴吧页面中的标题、首帖作者和有序正文。"""

    BLOCK_TAGS = {"blockquote", "div", "li", "p", "pre", "section"}
    VIDEO_EXTENSIONS = (".m3u8", ".mov", ".mp4")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.contents: list[OrderedContent] = []
        self.video_url = ""
        self.found_post = False
        self.found_content = False
        self._in_title = False
        self._title_parts: list[str] = []
        self._in_author = False
        self._author_tag = ""
        self._author_parts: list[str] = []
        self._post_depth = 0
        self._content_depth = 0
        self._ignored_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())

        if not self.title and tag in {"h1", "h3"} and "core_title_txt" in classes:
            self._in_title = True
            self.title = self._clean_text(attributes.get("title", ""))

        if not self.found_post and tag == "div" and "l_post" in classes:
            self.found_post = True
            self._post_depth = 1
            self.author = self._author_from_data_field(attributes.get("data-field", ""))
            return

        if not self._post_depth:
            return

        if tag == "div":
            self._post_depth += 1

        if not self.author and "p_author_name" in classes:
            self._in_author = True
            self._author_tag = tag

        if not self.found_content and "d_post_content" in classes:
            self.found_content = True
            self._content_depth = 1
            return

        if not self._content_depth:
            return

        if tag == "div":
            self._content_depth += 1
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return

        if tag in self.BLOCK_TAGS or tag == "br":
            self._flush_text()

        if tag == "img":
            self._flush_text()
            image_url = self._normalize_url(
                attributes.get("data-original")
                or attributes.get("data-src")
                or attributes.get("data-url")
                or attributes.get("src")
            )
            if image_url:
                self.contents.append(OrderedContent(kind="image", value=image_url))

        if tag in {"a", "embed", "source", "video"} and not self.video_url:
            for value in attributes.values():
                if video_url := self._extract_video_url(value):
                    self.video_url = video_url
                    break

    def handle_endtag(self, tag: str):
        if self._in_title and tag in {"h1", "h3"}:
            if not self.title:
                self.title = self._clean_text(" ".join(self._title_parts))
            self._title_parts.clear()
            self._in_title = False

        if self._in_author and tag == self._author_tag:
            self.author = self._clean_text(" ".join(self._author_parts))
            self._author_parts.clear()
            self._in_author = False
            self._author_tag = ""

        if not self._post_depth:
            return

        if self._content_depth:
            if tag in {"script", "style", "noscript"} and self._ignored_depth:
                self._ignored_depth -= 1
            elif not self._ignored_depth and tag in self.BLOCK_TAGS:
                self._flush_text()
            if tag == "div":
                self._content_depth -= 1
                if not self._content_depth:
                    self._flush_text()

        if tag == "div":
            self._post_depth -= 1

    def handle_data(self, data: str):
        if self._in_title and not self.title and (text := data.strip()):
            self._title_parts.append(text)
        if self._in_author and (text := data.strip()):
            self._author_parts.append(text)
        if self._content_depth and not self._ignored_depth and (text := data.strip()):
            self._text_parts.append(text)

    def close(self):
        super().close()
        self._flush_text()

    def _flush_text(self):
        text = self._clean_text(" ".join(self._text_parts))
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))

    @classmethod
    def _extract_video_url(cls, value: str) -> str:
        normalized = value.replace("\\/", "/").strip()
        if not normalized:
            return ""
        candidates = re.findall(r"https?://[^\s\"'<>]+", normalized)
        if normalized.startswith("//"):
            candidates.insert(0, f"https:{normalized}")
        elif normalized.startswith(("http://", "https://")):
            candidates.insert(0, normalized)
        for candidate in candidates:
            candidate = candidate.rstrip(",);]")
            path = candidate.lower().split("?", 1)[0]
            if path.endswith(cls.VIDEO_EXTENSIONS):
                return candidate
        return ""

    @staticmethod
    def _normalize_url(value: str) -> str:
        candidate = value.strip()
        if not candidate:
            return ""
        if candidate.startswith("//"):
            return f"https:{candidate}"
        if candidate.startswith("/"):
            return urljoin("https://tieba.baidu.com", candidate)
        if candidate.startswith(("http://", "https://")):
            return candidate
        return ""

    @staticmethod
    def _author_from_data_field(value: str) -> str:
        try:
            payload = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return ""
        author = payload.get("author") if isinstance(payload, dict) else None
        if not isinstance(author, dict):
            return ""
        for key in ("user_name", "user_nickname", "name_show"):
            if author_name := _TiebaPageParser._clean_text(str(author.get(key) or "")):
                return author_name
        return ""

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()


class TiebaParser(BaseParser):
    """解析百度贴吧普通帖子页面的楼主首帖。"""

    name = "tieba"
    display_name = "贴吧"
    cookie_config_key = "tieba_cookies"
    image_host_suffixes = ("baidu.com", "bdimg.com", "bdstatic.com", "bcebos.com")
    THREAD_PATTERN = re.compile(
        r"https?://(?:www\.)?tieba\.baidu\.com/p/(?P<thread_id>\d+)"
        r"(?![A-Za-z0-9])"
        r"(?:[/?#][^\s]*)?",
        re.IGNORECASE,
    )
    HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    SECURITY_MARKERS = (
        "百度安全验证",
        "bioc_options",
        "请输入验证码",
        "seccaptcha.baidu.com",
    )
    DELETED_MARKERS = ("该贴已被删除", "该帖已被删除", "帖子已被删除")
    UNAVAILABLE_MARKERS = (
        "本吧暂不开放",
        "您访问的贴子不存在",
        "您访问的帖子不存在",
        "该贴暂时无法访问",
        "该帖暂时无法访问",
    )

    async def match(self, context: ParseContext) -> bool:
        return self.THREAD_PATTERN.search(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        matched = self.THREAD_PATTERN.search(context.combined_text)
        if not matched:
            return ParseResult(platform=self.name, error="未找到贴吧帖子链接。")

        thread_id = matched.group("thread_id")
        page_url = f"https://tieba.baidu.com/p/{thread_id}"
        request_headers = {"Cookie": self._legacy_page_cookie_header()}

        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=False,
            headers=self.HEADERS,
        ) as client:
            response = await client.get(
                page_url,
                params={"see_lz": "1", "pn": "1"},
                headers=request_headers,
            )
            if response.status_code in {401, 403}:
                return self._cookie_failure_result()
            if 300 <= response.status_code < 400:
                return self._cookie_failure_result()
            response.raise_for_status()
            result = self._parse_page(response.text, thread_id)
            if result.error:
                return result
            return await self.materialize_images(result, client, page_url)

    def _legacy_page_cookie_header(self) -> str:
        """强制贴吧返回包含首帖正文的旧版服务端页面。"""
        cookie_parts = []
        legacy_switch_added = False
        raw_cookie = cookie_config_value(self.config, "tieba_cookies")
        for name, value in parse_cookie_header(raw_cookie):
            if name == "TIEBA_NEW_PC":
                if legacy_switch_added:
                    continue
                value = "0"
                legacy_switch_added = True
            cookie_parts.append(f"{name}={value.strip()}")

        if not legacy_switch_added:
            cookie_parts.append("TIEBA_NEW_PC=0")
        return "; ".join(cookie_parts)

    def _parse_page(self, html_text: str, thread_id: str) -> ParseResult:
        lowered_html = html_text.lower()
        if any(marker in lowered_html for marker in self.SECURITY_MARKERS):
            return self._cookie_failure_result()
        if any(marker in html_text for marker in self.DELETED_MARKERS):
            return ParseResult(platform=self.name, error="该贴吧帖子已被删除。")
        if any(marker in html_text for marker in self.UNAVAILABLE_MARKERS):
            return ParseResult(platform=self.name, error="该贴吧帖子当前无法访问。")

        parser = _TiebaPageParser()
        parser.feed(html_text)
        parser.close()
        if not parser.found_post:
            return ParseResult(
                platform=self.name,
                error="未找到贴吧首帖，页面可能需要登录或结构已变化。",
            )

        return ParseResult(
            platform=self.name,
            title=parser.title or f"贴吧帖子 {thread_id}",
            author=parser.author or "未知作者",
            video_url=parser.video_url,
            ordered_contents=parser.contents,
            extra_lines=(
                [] if parser.contents or parser.video_url else ["贴吧首帖正文为空。"]
            ),
        )

    def _cookie_failure_result(self) -> ParseResult:
        """生成不包含 Cookie 内容的贴吧访问失败结果。"""
        return ParseResult(platform=self.name, error=str(self.cookie_access_error()))
