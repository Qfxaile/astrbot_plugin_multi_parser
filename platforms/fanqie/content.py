"""番茄小说分享页 HTML、JSON 和元数据转换。"""

import json
import re
from collections.abc import Iterable, Mapping
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from .models import NovelMetadata


class SharePageParser(HTMLParser):
    """收集页面元标签和 JSON 脚本。"""

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.json_scripts: list[str] = []
        self._script_type = ""
        self._script_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
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


def extract_metadata(
    html_text: str,
    base_url: str,
    *,
    expected_book_id: str = "",
) -> NovelMetadata:
    parser = SharePageParser()
    parser.feed(html_text)
    metadata = NovelMetadata()
    for payload in iter_payloads(html_text, parser.json_scripts):
        for mapping in iter_mappings(payload):
            if expected_book_id and not mapping_matches_book(mapping, expected_book_id):
                continue
            candidate = metadata_from_mapping(mapping, base_url)
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
    return metadata.with_fallback(metadata_from_meta(parser.meta, base_url))


def iter_payloads(html_text: str, json_scripts: Iterable[str]) -> Iterable[object]:
    for script in json_scripts:
        try:
            yield json.loads(script)
        except (json.JSONDecodeError, RecursionError):
            continue
    pattern = re.compile(
        r"(?:window\.)?(?:__INITIAL_STATE__|__NEXT_DATA__|__NUXT__|_ROUTER_DATA)\s*=\s*"
    )
    for match in pattern.finditer(html_text):
        try:
            payload, _ = json.JSONDecoder().raw_decode(html_text, match.end())
        except (json.JSONDecodeError, RecursionError):
            continue
        yield payload


def iter_mappings(value: object, depth: int = 0) -> Iterable[Mapping[str, object]]:
    if depth > 20:
        return
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from iter_mappings(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from iter_mappings(child, depth + 1)


def mapping_matches_book(mapping: Mapping[str, object], expected_book_id: str) -> bool:
    value = mapping.get("bookId") or mapping.get("book_id")
    return isinstance(value, (str, int)) and str(value) == expected_book_id


def metadata_from_mapping(
    mapping: Mapping[str, object], base_url: str
) -> NovelMetadata:
    title = first_text(mapping, ("book_name", "bookName", "title", "name"))
    if not title:
        return NovelMetadata()
    return NovelMetadata(
        title=title,
        author=first_text(mapping, ("author", "author_name", "authorName")),
        description=first_text(
            mapping,
            ("abstract", "description", "book_abstract", "bookAbstract", "intro"),
        ),
        cover_url=absolute_url(
            first_text(
                mapping,
                (
                    "thumb_url",
                    "thumbUrl",
                    "thumbUri",
                    "cover_url",
                    "coverUrl",
                    "book_cover",
                    "bookCover",
                    "image",
                ),
            ),
            base_url,
        ),
    )


def metadata_from_meta(meta: Mapping[str, str], base_url: str) -> NovelMetadata:
    return NovelMetadata(
        title=clean_text(meta.get("og:title", "") or meta.get("twitter:title", "")),
        description=clean_text(
            meta.get("og:description", "") or meta.get("description", "")
        ),
        cover_url=absolute_url(
            meta.get("og:image", "") or meta.get("twitter:image", ""), base_url
        ),
    )


def first_text(mapping: Mapping[str, object], keys: Iterable[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = clean_text(str(value))
            if text:
                return text
    return ""


def clean_text(value: str) -> str:
    return " ".join(unescape(value).split())


def absolute_url(value: str, base_url: str) -> str:
    return urljoin(base_url, value.replace("\\u002F", "/")) if value else ""
