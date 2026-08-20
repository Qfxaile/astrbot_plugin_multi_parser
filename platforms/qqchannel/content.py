"""将腾讯频道帖子详情转换为统一图文与媒体结果。"""

from collections.abc import Mapping
from urllib.parse import urlsplit

from ...core.contracts import OrderedContent, ParseResult
from ...core.http import is_trusted_https_url

IMAGE_HOST_SUFFIXES = (
    "qpic.cn",
    "photo.store.qq.com",
    "gtimg.cn",
    "myqcloud.com",
    "qcloud.com",
)
VIDEO_HOST_SUFFIXES = ("qq.com", "gtimg.cn", "myqcloud.com", "qcloud.com")
AUDIO_HOST_SUFFIXES = VIDEO_HOST_SUFFIXES

RICH_TEXT = 1
RICH_IMAGE = 2
RICH_VIDEO = 3
RICH_URL = 4
RICH_EMOJI = 5
RICH_AT = 6
RICH_CHANNEL = 7
RICH_TOPIC = 8
RICH_LINE_BREAK = 10
ORDERED_LIST = 1
BULLET_LIST = 2


def build_result(feed: Mapping[str, object], *, fallback_title: str) -> ParseResult:
    """按腾讯频道详情结构保留正文段落和媒体顺序。"""
    title = _clean_inline_text(_contents_text(feed.get("title")))
    author = _clean_inline_text(_mapping(feed.get("poster")).get("nick"))
    contents, video_urls = _rich_contents(feed)

    if not contents:
        body = _clean_block_text(_contents_text(feed.get("contents")))
        if body and _clean_inline_text(body) != title:
            contents.append(OrderedContent(kind="text", value=body))

    if contents and contents[0].kind == "text":
        if _clean_inline_text(contents[0].value) == title:
            contents.pop(0)

    seen_images = {
        item.value for item in contents if item.kind == "image" and item.value
    }
    for image in _sorted_media(feed.get("images")):
        image_url = _image_url(image)
        if image_url and image_url not in seen_images:
            contents.append(OrderedContent(kind="image", value=image_url))
            seen_images.add(image_url)

    seen_videos = set(video_urls)
    for video in _sorted_media(feed.get("videos")):
        video_url = _video_url(video)
        if not video_url or video_url in seen_videos:
            continue
        _append_video(contents, video_urls, video_url)
        seen_videos.add(video_url)

    audio_url = ""
    for audio in _sorted_media(feed.get("audios")):
        audio_url = _audio_url(audio)
        if audio_url:
            break

    return ParseResult(
        platform="qqchannel",
        title=title or _clean_inline_text(fallback_title) or "腾讯频道帖子",
        author=author,
        ordered_contents=contents,
        video_url=video_urls[0] if video_urls else "",
        video_download_host_suffixes=VIDEO_HOST_SUFFIXES,
        audio_url=audio_url,
        extra_lines=[]
        if contents or video_urls or audio_url
        else ["腾讯频道帖子正文为空。"],
    )


def _rich_contents(
    feed: Mapping[str, object],
) -> tuple[list[OrderedContent], list[str]]:
    styled = _mapping(feed.get("content_with_style"))
    paragraphs = _mapping_list(styled.get("paragraphs"))
    contents: list[OrderedContent] = []
    image_urls: list[str] = []
    video_urls: list[str] = []
    ordered_number = 0

    for paragraph in paragraphs:
        props = _mapping(paragraph.get("props"))
        list_type = _int_value(props.get("list_type"))
        ordered_number = ordered_number + 1 if list_type == ORDERED_LIST else 0
        prefix = (
            f"{ordered_number}. "
            if list_type == ORDERED_LIST
            else "- "
            if list_type == BULLET_LIST
            else ""
        )
        text_parts: list[str] = []
        prefix_pending = bool(prefix)

        for element in _mapping_list(paragraph.get("elems")):
            element_type = _int_value(element.get("type"))
            if element_type == RICH_IMAGE:
                if _flush_text(contents, text_parts, prefix if prefix_pending else ""):
                    prefix_pending = False
                image_url = _image_url(
                    _mapping(_mapping(element.get("image")).get("image"))
                )
                if image_url and image_url not in image_urls:
                    contents.append(OrderedContent(kind="image", value=image_url))
                    image_urls.append(image_url)
                continue
            if element_type == RICH_VIDEO:
                if _flush_text(contents, text_parts, prefix if prefix_pending else ""):
                    prefix_pending = False
                video_url = _video_url(
                    _mapping(_mapping(element.get("video")).get("video"))
                )
                if video_url and video_url not in video_urls:
                    _append_video(contents, video_urls, video_url)
                continue
            text_parts.append(_rich_text(element, element_type))
        _flush_text(contents, text_parts, prefix if prefix_pending else "")

    return contents, video_urls


def _flush_text(
    contents: list[OrderedContent], text_parts: list[str], prefix: str
) -> bool:
    text = _clean_block_text("".join(text_parts))
    text_parts.clear()
    if not text:
        return False
    contents.append(OrderedContent(kind="text", value=f"{prefix}{text}"))
    return True


def _append_video(
    contents: list[OrderedContent], video_urls: list[str], video_url: str
) -> None:
    if video_urls:
        contents.append(OrderedContent(kind="text", value=f"视频链接: {video_url}"))
    video_urls.append(video_url)


def _rich_text(element: Mapping[str, object], element_type: int) -> str:
    if element_type == RICH_TEXT:
        return str(
            _mapping(_mapping(element.get("text")).get("text_content")).get("text", "")
        )
    if element_type == RICH_URL:
        value = _mapping(_mapping(element.get("url")).get("url_content"))
        url = _safe_display_url(value.get("url"))
        label = str(value.get("displayText") or value.get("display_text") or "")
        if label and url and label != url:
            return f"{label} ({url})"
        return label or url
    if element_type == RICH_EMOJI:
        return "[表情]"
    if element_type == RICH_AT:
        value = _mapping(_mapping(element.get("at")).get("at_content"))
        user = _mapping(value.get("user"))
        role = _mapping(value.get("role_group_id"))
        guild = _mapping(value.get("guild_info"))
        name = user.get("nick") or role.get("name") or guild.get("name")
        return f"@{name}" if name else "@用户"
    if element_type == RICH_CHANNEL:
        value = _mapping(_mapping(element.get("channel")).get("channel_content"))
        info = _mapping(value.get("channelInfo") or value.get("channel_info"))
        name = str(info.get("name") or "").lstrip("*#")
        return f"#{name}" if name else "[频道]"
    if element_type == RICH_TOPIC:
        value = _mapping(_mapping(element.get("topic")).get("topic_content"))
        name = str(value.get("topic_name") or value.get("topicName") or "")
        return f"#{name}" if name else "[话题]"
    if element_type == RICH_LINE_BREAK:
        return "\n"
    return ""


def _contents_text(value: object) -> str:
    parts: list[str] = []
    for item in _mapping_list(_mapping(value).get("contents")):
        if text := _mapping(item.get("text_content")).get("text"):
            parts.append(str(text))
            continue
        if item.get("emoji_content"):
            parts.append("[表情]")
            continue
        if topic := _mapping(item.get("topic_content")).get("topic_name"):
            parts.append(f"#{topic}")
            continue
        if url_content := _mapping(item.get("url_content")):
            url = _safe_display_url(url_content.get("url"))
            label = str(
                url_content.get("displayText") or url_content.get("display_text") or ""
            )
            parts.append(label or url)
    return "".join(parts)


def _image_url(value: Mapping[str, object]) -> str:
    for key in ("picUrl", "pic_url", "url", "cover_url"):
        candidate = str(value.get(key) or "")
        if is_trusted_https_url(candidate, IMAGE_HOST_SUFFIXES):
            return candidate
    variants = sorted(
        _mapping_list(value.get("vecImageUrl") or value.get("vec_image_url")),
        key=lambda item: _int_value(item.get("width")),
        reverse=True,
    )
    for variant in variants:
        candidate = str(variant.get("url") or "")
        if is_trusted_https_url(candidate, IMAGE_HOST_SUFFIXES):
            return candidate
    return ""


def _video_url(value: Mapping[str, object]) -> str:
    for key in ("playUrl", "play_url", "videoUrl", "video_url", "url"):
        candidate = str(value.get(key) or "")
        if is_trusted_https_url(candidate, VIDEO_HOST_SUFFIXES):
            return candidate
    return ""


def _audio_url(value: Mapping[str, object]) -> str:
    for key in ("playUrl", "play_url", "audioUrl", "audio_url", "url"):
        candidate = str(value.get(key) or "")
        if is_trusted_https_url(candidate, AUDIO_HOST_SUFFIXES):
            return candidate
    return ""


def _safe_display_url(value: object) -> str:
    candidate = str(value or "").strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return ""
    return candidate


def _sorted_media(value: object) -> list[Mapping[str, object]]:
    return sorted(
        _mapping_list(value),
        key=lambda item: _int_value(item.get("display_index"), default=1 << 30),
    )


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _int_value(value: object, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean_inline_text(value: object) -> str:
    return " ".join(str(value or "").split())[:300]


def _clean_block_text(value: object) -> str:
    lines = [" ".join(line.split()) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line).strip()
