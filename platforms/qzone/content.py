"""提取 QQ 空间公开说说页面中的正文和可信媒体。"""

import html
import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from ...core.contracts import OrderedContent, ParseResult
from ...core.http import is_trusted_https_url

IMAGE_HOST_SUFFIXES = ("qpic.cn", "photo.store.qq.com", "gtimg.cn")
VIDEO_HOST_SUFFIXES = ("qq.com", "gtimg.cn")
BACKGROUND_URL_PATTERN = re.compile(
    r"background-image\s*:\s*url\(\s*(['\"]?)(.*?)\1\s*\)",
    re.IGNORECASE,
)
FRONT_PAGE_PATTERN = re.compile(r"\bvar\s+FrontPage\s*=")
FRONT_PAGE_DATA_PATTERN = re.compile(r"\bdata\s*:\s*")
VOID_TAGS = {
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
    "source",
    "track",
    "wbr",
}


def _normalize_media_url(value: str, host_suffixes: tuple[str, ...]) -> str:
    candidate = html.unescape(value).strip().strip("\"'")
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if not is_trusted_https_url(candidate, host_suffixes):
        return ""
    return candidate


def _prefer_large_psc_image_url(url: str) -> str:
    """将 QQ 空间 psc 中图或小图地址提升为大图规格。"""
    parsed = urlsplit(url)
    if parsed.path != "/psc":
        return url
    image_path, separator, parameters = parsed.query.partition("&")
    if not image_path.endswith(("/m", "/s")):
        return url
    query = f"{image_path[:-1]}b{separator}{parameters}"
    return urlunsplit(parsed._replace(query=query))


def _front_page_image_urls(html_text: str) -> list[str]:
    """提取传统分享页内嵌的完整图片列表。"""
    front_page = FRONT_PAGE_PATTERN.search(html_text)
    if front_page is None:
        return []
    data_property = FRONT_PAGE_DATA_PATTERN.search(html_text, front_page.end())
    if data_property is None:
        return []
    try:
        payload, _ = json.JSONDecoder().raw_decode(html_text, data_property.end())
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(payload, Mapping):
        return []
    feed = payload.get("data")
    if not isinstance(feed, Mapping):
        return []

    original = feed.get("cell_original")
    candidates = (original, feed) if isinstance(original, Mapping) else (feed,)
    for candidate in candidates:
        cell_pic = candidate.get("cell_pic")
        if not isinstance(cell_pic, Mapping):
            continue
        picdata = cell_pic.get("picdata")
        if not isinstance(picdata, list):
            continue
        image_urls = _picdata_image_urls(picdata)
        if image_urls:
            return image_urls
    return []


def _picdata_image_urls(picdata: list[object]) -> list[str]:
    image_urls: list[str] = []
    seen: set[str] = set()
    for item in picdata:
        if not isinstance(item, Mapping):
            continue
        image_url = _picdata_image_url(item)
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        image_urls.append(image_url)
    return image_urls


def _picdata_image_url(item: Mapping[str, Any]) -> str:
    photourl = item.get("photourl")
    if isinstance(photourl, Mapping):
        preferred_keys = ("1", "0", "11")
        values = [photourl.get(key) for key in preferred_keys]
        values.extend(
            value for key, value in photourl.items() if str(key) not in preferred_keys
        )
        for value in values:
            if not isinstance(value, Mapping):
                continue
            image_url = _normalize_media_url(
                str(value.get("url", "")),
                IMAGE_HOST_SUFFIXES,
            )
            if image_url:
                return _prefer_large_psc_image_url(image_url)

    image_url = _normalize_media_url(
        str(item.get("sloc", "")),
        IMAGE_HOST_SUFFIXES,
    )
    return _prefer_large_psc_image_url(image_url)


def _expand_page_images(
    contents: list[OrderedContent],
    complete_image_urls: list[str],
) -> None:
    visible_image_count = sum(item.kind == "image" for item in contents)
    if len(complete_image_urls) <= visible_image_count:
        return

    expanded_images = [
        OrderedContent(kind="image", value=image_url)
        for image_url in complete_image_urls
    ]
    merged: list[OrderedContent] = []
    inserted = False
    for item in contents:
        if item.kind == "image":
            if not inserted:
                merged.extend(expanded_images)
                inserted = True
            continue
        merged.append(item)
    if not inserted:
        merged.extend(expanded_images)
    contents[:] = merged


class _QzonePageParser(HTMLParser):
    """仅遍历主说说节点，避免把评论和互动信息混入正文。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found_feed = False
        self.author = ""
        self.contents: list[OrderedContent] = []
        self.video_url = ""
        self._feed_depth = 0
        self._body_depth = 0
        self._images_depth = 0
        self._author_tag = ""
        self._author_parts: list[str] = []
        self._text_depth = 0
        self._text_parts: list[str] = []
        self._image_urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if not self._feed_depth:
            if tag == "div" and {"feed", "dataItem"}.issubset(classes):
                self.found_feed = True
                self._feed_depth = 1
            return

        if tag == "div":
            self._feed_depth += 1

        if not self.author and not self._author_tag and "username" in classes:
            self._author_tag = tag

        if not self._body_depth:
            if tag == "div" and "feed-bd" in classes:
                self._body_depth = 1
            return

        if tag == "div":
            self._body_depth += 1
            if self._images_depth:
                self._images_depth += 1
            elif "images" in classes:
                self._images_depth = 1

        started_text = False
        if not self._text_depth and "txt" in classes:
            self._text_depth = 1
            started_text = True
        elif self._text_depth and tag not in VOID_TAGS:
            self._text_depth += 1

        if self._text_depth and tag == "br":
            self._text_parts.append("\n")

        if self._images_depth or tag == "img":
            self._append_image(attributes)
        if not self.video_url:
            self.video_url = self._video_from_attributes(tag, attributes)

        if started_text and tag in VOID_TAGS:
            self._flush_text()

    def handle_endtag(self, tag: str):
        if self._author_tag and tag == self._author_tag:
            self.author = self._clean_inline_text(" ".join(self._author_parts))
            self._author_parts.clear()
            self._author_tag = ""

        if self._text_depth and tag not in VOID_TAGS:
            self._text_depth -= 1
            if not self._text_depth:
                self._flush_text()

        if not self._feed_depth:
            return
        if tag == "div":
            if self._images_depth:
                self._images_depth -= 1
            if self._body_depth:
                self._body_depth -= 1
            self._feed_depth -= 1

    def handle_data(self, data: str):
        if self._author_tag:
            self._author_parts.append(data)
        if self._text_depth:
            self._text_parts.append(data)

    def close(self):
        super().close()
        self._flush_text()

    def _append_image(self, attributes: dict[str, str]) -> None:
        image_url = self._media_from_attributes(attributes, IMAGE_HOST_SUFFIXES)
        image_url = _prefer_large_psc_image_url(image_url)
        if not image_url or image_url in self._image_urls:
            return
        self._image_urls.add(image_url)
        self.contents.append(OrderedContent(kind="image", value=image_url))

    @staticmethod
    def _media_from_attributes(
        attributes: dict[str, str],
        host_suffixes: tuple[str, ...],
    ) -> str:
        for key in (
            "data-original",
            "data-src",
            "data-url",
            "data-feedlazy",
            "src",
        ):
            if url := _normalize_media_url(attributes.get(key, ""), host_suffixes):
                return url
        if matched := BACKGROUND_URL_PATTERN.search(attributes.get("style", "")):
            return _normalize_media_url(matched.group(2), host_suffixes)
        return ""

    @staticmethod
    def _video_from_attributes(tag: str, attributes: dict[str, str]) -> str:
        for key in ("data-playvideo", "data-video", "data-videourl"):
            if url := _normalize_media_url(
                attributes.get(key, ""),
                VIDEO_HOST_SUFFIXES,
            ):
                return url
        if tag in {"video", "source"}:
            return _QzonePageParser._media_from_attributes(
                attributes,
                VIDEO_HOST_SUFFIXES,
            )
        return ""

    def _flush_text(self) -> None:
        if not self._text_parts:
            return
        lines = [
            self._clean_inline_text(line)
            for line in "".join(self._text_parts).splitlines()
        ]
        self._text_parts.clear()
        text = "\n".join(line for line in lines if line)
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))

    @staticmethod
    def _clean_inline_text(value: str) -> str:
        return " ".join(html.unescape(value).split())


class QzonePageContent:
    """把 QQ 空间服务端页面转换为统一解析结果。"""

    def _parse_page(
        self,
        html_text: str,
        res_uin: str,
        title: str = "QQ空间说说",
    ) -> ParseResult:
        page = _QzonePageParser()
        page.feed(html_text)
        page.close()
        if not page.found_feed:
            return ParseResult(
                platform=self.name,
                error="未找到QQ空间说说内容，页面可能需要登录或结构已变化。",
            )
        _expand_page_images(page.contents, _front_page_image_urls(html_text))
        author = page.author
        if not author or author.lower() == "unknown":
            author = f"QQ {res_uin}"
        return ParseResult(
            platform=self.name,
            title=title,
            author=author,
            video_url=page.video_url,
            ordered_contents=page.contents,
            extra_lines=[]
            if page.contents or page.video_url
            else ["QQ空间说说正文为空。"],
        )

    def _parse_universal_page(self, html_text: str) -> ParseResult:
        script = _NuxtDataParser()
        script.feed(html_text)
        script.close()
        template = _find_universal_template(script.payload)
        if template is None:
            return ParseResult(
                platform=self.name,
                error="未找到QQ空间动态内容，页面可能已失效或结构已变化。",
            )

        contents: list[OrderedContent] = []
        for item in _mapping_list(template.get("content")):
            text = str(item.get("content", "")).strip()
            if text:
                contents.append(OrderedContent(kind="text", value=text))

        image_urls: set[str] = set()
        video_url = ""
        for item in _mapping_list(template.get("album")):
            if not video_url:
                video_url = _normalize_media_url(
                    str(item.get("video_url", "")),
                    VIDEO_HOST_SUFFIXES,
                )
            if item.get("video_url"):
                continue
            image_url = _universal_image_url(item)
            if image_url and image_url not in image_urls:
                image_urls.add(image_url)
                contents.append(OrderedContent(kind="image", value=image_url))

        author = str(template.get("nickname", "")).strip()
        if not author or author.lower() == "unknown":
            uin = _uin_from_avatar(str(template.get("avatar", "")))
            author = f"QQ {uin}" if uin else "QQ用户"
        title = str(template.get("album_name") or template.get("title") or "").strip()
        return ParseResult(
            platform=self.name,
            title=title or "QQ空间动态",
            author=author,
            video_url=video_url,
            ordered_contents=contents,
            extra_lines=[] if contents or video_url else ["QQ空间动态正文为空。"],
        )


class _NuxtDataParser(HTMLParser):
    """提取 Universal Share 服务端渲染的 Nuxt JSON 数据。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.payload = ""
        self._in_payload = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attributes = {key: value or "" for key, value in attrs}
        if tag == "script" and attributes.get("id") == "__NUXT_DATA__":
            self._in_payload = True

    def handle_endtag(self, tag: str):
        if tag == "script" and self._in_payload:
            self.payload = "".join(self._parts)
            self._in_payload = False

    def handle_data(self, data: str):
        if self._in_payload:
            self._parts.append(data)


def _find_universal_template(payload: str) -> Mapping[str, Any] | None:
    try:
        values = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(values, list):
        return None
    for value in values:
        if not isinstance(value, str) or not value.startswith("{"):
            continue
        try:
            candidate = json.loads(value)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(candidate, Mapping)
            and isinstance(candidate.get("content"), list)
            and isinstance(candidate.get("album"), list)
        ):
            return candidate
    return None


def _mapping_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _universal_image_url(item: Mapping[str, Any]) -> str:
    for key in ("big_img", "small_img"):
        image = item.get(key)
        if isinstance(image, Mapping):
            url = _normalize_media_url(
                str(image.get("url", "")),
                IMAGE_HOST_SUFFIXES,
            )
            if url:
                return url
    return ""


def _uin_from_avatar(avatar_url: str) -> str:
    if not is_trusted_https_url(avatar_url, ("qlogo.cn",)):
        return ""
    values = parse_qs(urlsplit(avatar_url).query).get("nk", [])
    if len(values) == 1 and values[0].isdigit():
        return values[0]
    return ""
