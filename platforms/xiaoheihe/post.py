import json
from html.parser import HTMLParser

import httpx

from ...core.contracts import OrderedContent, ParseResult
from .common import clean_text, normalize_image_url, normalize_media_url


class PostHTMLParser(HTMLParser):
    """按帖子正文片段的可见顺序提取文本和图片候选。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.contents: list[OrderedContent] = []
        self._text_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "br"}:
            self._flush_text()
        if tag == "img":
            self._flush_text()
            attributes = dict(attrs)
            image_url = str(
                attributes.get("data-original")
                or attributes.get("data-src")
                or attributes.get("src")
                or ""
            )
            if image_url:
                self.contents.append(OrderedContent(kind="image", value=image_url))

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if not self._ignored_depth and tag in {"p", "div", "li", "blockquote"}:
            self._flush_text()

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and (text := data.strip()):
            self._text_parts.append(text)

    def close(self) -> None:
        super().close()
        self._flush_text()

    def _flush_text(self) -> None:
        text = " ".join(self._text_parts).strip()
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))


def parse_post_payload(payload: object) -> ParseResult:
    link = payload.get("link") if isinstance(payload, dict) else None
    if not isinstance(link, dict):
        raise ValueError("小黑盒 link/tree 缺少 link 节点")
    user = link.get("user")
    author = "未知作者"
    if isinstance(user, dict):
        author = (
            clean_text(str(user.get("username") or user.get("nickname") or ""))
            or author
        )
    contents = parse_post_contents(link.get("text"))
    video_url = normalize_media_url(link.get("video_url"))
    if not link.get("has_video"):
        video_url = ""
    description = clean_text(str(link.get("description") or "")) if video_url else ""
    return ParseResult(
        platform="xiaoheihe",
        title=clean_text(str(link.get("title") or "")) or "小黑盒帖子",
        author=author,
        description=description,
        video_url=video_url,
        ordered_contents=contents,
        extra_lines=[] if contents or video_url else ["未找到可发送的媒体。"],
    )


def parse_post_contents(raw_text: object) -> list[OrderedContent]:
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []
    try:
        blocks = json.loads(raw_text)
    except json.JSONDecodeError:
        text = clean_text(raw_text)
        return [OrderedContent(kind="text", value=text)] if text else []
    if not isinstance(blocks, list):
        text = clean_text(raw_text)
        return [OrderedContent(kind="text", value=text)] if text else []

    contents: list[OrderedContent] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if str(block.get("type") or "") == "img":
            append_image(contents, block.get("url"))
            continue
        fragment = str(block.get("text") or "")
        if not fragment:
            continue
        parser = PostHTMLParser()
        parser.feed(fragment)
        parser.close()
        for item in parser.contents:
            if item.kind == "image":
                append_image(contents, item.value)
            elif value := clean_text(item.value):
                contents.append(OrderedContent(kind="text", value=value))
    return contents


def append_image(
    contents: list[OrderedContent],
    candidate: object,
) -> None:
    image_url = normalize_image_url(candidate)
    if image_url:
        contents.append(OrderedContent(kind="image", value=image_url))


class XiaoheihePostContent:
    """请求并解析小黑盒社区帖子。"""

    async def _parse_post_by_id(self, link_id: str) -> ParseResult:
        params = {
            "app": "heybox",
            "os_type": "web",
            "x_app": "heybox_website",
            "x_client_type": "web",
            "x_os_type": "Windows",
            "x_client_version": "",
            "client_type": "web",
            "web_version": "3.0",
            "version": "999.0.4",
            "link_id": link_id,
            "is_first": "1",
            "page": "1",
            "index": "1",
            "limit": "20",
            "owner_only": "0",
            **self._sign_path("/bbs/app/link/tree"),
        }
        referer = f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=self.HEADERS,
        ) as client:
            response = await client.get(
                "https://api.xiaoheihe.cn/bbs/app/link/tree",
                params=params,
                headers=self._request_cookie_headers(),
            )
            self.raise_for_response_status(response)
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                self._raise_for_payload_error(payload)
                raise ValueError("小黑盒 link/tree 请求失败")
            result_root = payload.get("result")
            if not isinstance(result_root, dict):
                raise ValueError("小黑盒 link/tree 结果为空")
            result = parse_post_payload(result_root)
            return await self.materialize_images(result, client, referer)
