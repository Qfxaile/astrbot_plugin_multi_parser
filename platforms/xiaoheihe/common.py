import html
import re
from urllib.parse import urlsplit, urlunsplit


def clean_text(text: str) -> str:
    """清理小黑盒内容中的实体和冗余空白。"""
    value = html.unescape(text.replace("\xa0", " "))
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def normalize_media_url(value: object) -> str:
    """规范化小黑盒媒体地址并过滤非 HTTP(S) 候选。"""
    if not isinstance(value, str) or not value:
        return ""
    normalized = html.unescape(value).strip()
    if normalized.startswith("//"):
        normalized = f"https:{normalized}"
    return normalized if normalized.startswith(("http://", "https://")) else ""


def normalize_image_url(value: object) -> str:
    """规范化小黑盒图片地址并统一等价的图片域名。"""
    normalized = normalize_media_url(value)
    if not normalized:
        return ""
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        return normalized
    hostname = (parsed.hostname or "").lower()
    if hostname == "imgheybox1.max-c.com":
        parsed = parsed._replace(netloc="imgheybox.max-c.com")
    return urlunsplit(parsed)


def image_dedup_key(url: str) -> str:
    """生成忽略查询参数和等价图片域名的去重键。"""
    if not url:
        return ""
    return url.split("?", 1)[0].replace("imgheybox1.max-c.com", "imgheybox.max-c.com")
