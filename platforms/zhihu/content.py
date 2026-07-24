import re
from html.parser import HTMLParser

from ...core.contracts import OrderedContent
from .common import media_key, normalize_media_url, normalize_text


class ZhihuHTMLParser(HTMLParser):
    """提取知乎 HTML 中按文档顺序排列的可见正文和媒体。"""

    def __init__(self, page_url: str | None = None):
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.contents: list[OrderedContent] = []
        self.video_urls: list[str] = []
        self._text_parts: list[str] = []
        self._seen_videos: set[str] = set()
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in {"script", "style", "noscript", "svg", "iframe", "audio"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        attributes = dict(attrs)
        if tag in {
            "p",
            "div",
            "li",
            "blockquote",
            "h1",
            "h2",
            "h3",
            "pre",
            "br",
        }:
            self._flush_text()
        if tag == "img":
            self._flush_text()
            self._append_image(
                attributes.get("data-original")
                or attributes.get("data-actualsrc")
                or attributes.get("data-src")
                or attributes.get("src")
            )
        if tag in {"video", "source"}:
            self._append_video(attributes.get("src"))
        elif tag == "a" and attributes.get("data-video-id"):
            self._append_video(attributes.get("href"))

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript", "svg", "iframe", "audio"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if not self._ignored_depth and tag in {
            "p",
            "div",
            "li",
            "blockquote",
            "h1",
            "h2",
            "h3",
            "pre",
        }:
            self._flush_text()

    def handle_data(self, data: str):
        if not self._ignored_depth and (text := normalize_text(data)):
            self._text_parts.append(text)

    def close(self):
        super().close()
        self._flush_text()

    def _flush_text(self):
        text = normalize_text(" ".join(self._text_parts), keep_newlines=True)
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))

    def _append_image(self, candidate: str | None):
        image_url = normalize_media_url(str(candidate or ""), self.page_url)
        if image_url:
            self.contents.append(OrderedContent(kind="image", value=image_url))

    def _append_video(self, candidate: str | None):
        video_url = normalize_media_url(str(candidate or ""), self.page_url)
        if not _looks_like_video(video_url):
            return
        key = media_key(video_url)
        if key and key not in self._seen_videos:
            self._seen_videos.add(key)
            self.video_urls.append(video_url)


def _looks_like_video(url: str) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return bool(
        re.search(r"\.(?:mp4|m3u8|mov|webm)(?:$|[?#])", lowered)
        or "video.zhihu.com" in lowered
        or "/playlist.m3u8" in lowered
    )


def parse_html_content(
    value: str, page_url: str | None = None
) -> tuple[list[OrderedContent], list[str]]:
    """一次遍历提取知乎正文和视频 URL。"""
    parser = ZhihuHTMLParser(page_url)
    parser.feed(value or "")
    parser.close()
    return parser.contents, parser.video_urls


def parse_html_body(value: str, page_url: str | None = None) -> list[OrderedContent]:
    return parse_html_content(value, page_url)[0]


def extract_html_video_urls(value: str, page_url: str | None = None) -> list[str]:
    return parse_html_content(value, page_url)[1]
