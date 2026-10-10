"""将 AstrBot 消息事件转换为平台无关的解析上下文。"""

from astrbot.api.event import AstrMessageEvent

from ..core.contracts import ParseContext
from .share_card import ShareCardExtractor


def extract_context(event: AstrMessageEvent) -> ParseContext:
    """从 AstrBot 事件中提取文本与分享卡片上下文。"""
    # 不同协议适配器可能以字典或对象表示原始消息，服务层在进入核心解析前统一结构。
    raw = getattr(event.message_obj, "raw_message", None)
    if isinstance(raw, dict):
        raw_message = raw.get("message", [])
    else:
        raw_message = getattr(raw, "message", []) if raw else []

    text_parts = [event.message_str]
    json_urls: list[str] = []
    json_previews: list[str] = []
    json_titles: list[str] = []
    json_metadata: list[dict[str, str]] = []

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
            url, title, preview, metadata = ShareCardExtractor.extract(str(json_data))
            if url:
                json_urls.append(url)
                json_previews.append(preview)
                json_titles.append(title)
                json_metadata.append(metadata)

    return ParseContext(
        text="\n".join(part for part in text_parts if part).strip(),
        json_urls=json_urls,
        json_previews=json_previews,
        json_titles=json_titles,
        json_metadata=json_metadata,
    )


def _extract_json_card(data: str) -> tuple[str, str, str, dict[str, str]]:
    """从 QQ JSON 分享卡片中提取公开展示字段和非敏感标识。"""
    return ShareCardExtractor.extract(data)


def _extract_feed_id(feed: dict) -> str:
    """仅保留腾讯频道帖子公开标识，不传递卡片令牌或用户标识。"""
    return ShareCardExtractor.feed_id(feed)


def _extract_json_url_and_preview(data: str) -> tuple[str, str]:
    """兼容现有调用，只返回 QQ JSON 分享卡片的链接和预览图。"""
    url, _, preview, _ = _extract_json_card(data)
    return url, preview
