"""将 AstrBot 消息事件转换为平台无关的解析上下文。"""

import json

from astrbot.api.event import AstrMessageEvent

from ..core.contracts import ParseContext


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
            url, preview = _extract_json_url_and_preview(str(json_data))
            if url:
                json_urls.append(url)
            if preview:
                json_previews.append(preview)

    return ParseContext(
        text="\n".join(part for part in text_parts if part).strip(),
        json_urls=json_urls,
        json_previews=json_previews,
    )


def _extract_json_url_and_preview(data: str) -> tuple[str, str]:
    """从 QQ JSON 分享卡片中提取跳转链接和预览文本。"""
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    meta = payload.get("meta", {})
    if not isinstance(meta, dict):
        return "", ""
    detail = meta.get("detail_1", {})
    news = meta.get("news", {})
    miniapp = meta.get("miniapp", {})
    detail = detail if isinstance(detail, dict) else {}
    news = news if isinstance(news, dict) else {}
    miniapp = miniapp if isinstance(miniapp, dict) else {}
    url = (
        detail.get("qqdocurl", "")
        or news.get("jumpUrl", "")
        or miniapp.get("pcJumpUrl", "")
        or miniapp.get("legacyUrl", "")
    )
    preview = news.get("preview", "") or miniapp.get("preview", "")
    return str(url or ""), str(preview or "")
