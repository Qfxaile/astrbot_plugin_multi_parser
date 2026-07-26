import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ...core.contracts import OrderedContent, ParseResult


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
            if candidate.lower().split("?", 1)[0].endswith(cls.VIDEO_EXTENSIONS):
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


class TiebaThreadContent:
    """把贴吧首帖的混合媒体页面转换为统一解析结果。"""

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
            extra_lines=[]
            if parser.contents or parser.video_url
            else ["贴吧首帖正文为空。"],
        )
