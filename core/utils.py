import json
import re

from astrbot.api.event import AstrMessageEvent

from ..models import ParseContext

LINK_PATTERN = re.compile(r"(?:https?://|www\.)[^\s<>\[\]()（）]+", re.IGNORECASE)
LINK_TRAILING_PUNCTUATION = ".,!?;:，。！？；："


def extract_context(event: AstrMessageEvent) -> ParseContext:
    """从 AstrBot 事件中提取平台无关的文本与分享卡片上下文。"""
    # 不同 AstrBot 适配器可能把原始消息表示为字典或对象，这里统一取出消息段列表。
    raw = getattr(event.message_obj, "raw_message", None)
    if isinstance(raw, dict):
        raw_message = raw.get("message", [])
    else:
        raw_message = getattr(raw, "message", []) if raw else []

    text_parts = [event.message_str]
    json_urls: list[str] = []
    json_previews: list[str] = []

    # 同时收集普通文本和 JSON 分享卡片中的链接，供各平台解析器统一匹配。
    for segment in raw_message:
        if isinstance(segment, dict):
            segment_type = segment.get("type")
            data = segment.get("data", {})
        else:
            segment_type = getattr(segment, "type", "")
            data = getattr(segment, "data", {})

        if segment_type == "text":
            text = (
                data.get("text", "")
                if isinstance(data, dict)
                else getattr(data, "text", "")
            )
            text_parts.append(str(text))
        elif segment_type == "json":
            json_data = (
                data.get("data", "")
                if isinstance(data, dict)
                else getattr(data, "data", "")
            )
            url, preview = extract_json_url_and_preview(str(json_data))
            if url:
                json_urls.append(url)
            if preview:
                json_previews.append(preview)

    return ParseContext(
        text="\n".join(part for part in text_parts if part).strip(),
        json_urls=json_urls,
        json_previews=json_previews,
    )


def extract_json_url_and_preview(data: str) -> tuple[str, str]:
    """从 QQ JSON 分享卡片中提取跳转链接和预览文本。"""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return "", ""
    url = payload.get("meta", {}).get("detail_1", {}).get(
        "qqdocurl", ""
    ) or payload.get("meta", {}).get("news", {}).get("jumpUrl", "")
    preview = payload.get("meta", {}).get("news", {}).get("preview", "")
    return str(url or ""), str(preview or "")


def replace_links(
    text: str,
    replacement: str = "[详细内容请打开原链接查看]",
) -> str:
    """替换可见文本中的网页链接，并保留链接末尾的标点。"""

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        link = value.rstrip(LINK_TRAILING_PUNCTUATION)
        trailing = value[len(link) :]
        return f"{replacement}{trailing}"

    return LINK_PATTERN.sub(replace, text).strip()
