import html
import re
from urllib.parse import urlparse

import httpx

from ...core.http import build_cookies, cookie_config_value


def normalize_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    return value if value.startswith(("http://", "https://")) else ""


class WeiboContentSupport:
    """提供微博各内容类型共享的 URL、Cookie 和文本能力。"""

    AUTH_PATH_MARKERS = ("/login", "/passport/", "/signin")

    def _timeout(self) -> float:
        return self.request_timeout

    def _cookies(self) -> httpx.Cookies:
        return build_cookies(
            cookie_config_value(self.config, "weibo_cookies"),
            (".weibo.com", ".weibo.cn"),
        )

    @classmethod
    def _is_trusted_weibo_url(cls, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in {"weibo.com", "weibo.cn"} or host.endswith(
            (".weibo.com", ".weibo.cn")
        )

    @staticmethod
    def _base62_encode(number: int) -> str:
        """将非负整数编码为微博使用的 base62 字符串。"""
        if number < 0:
            raise ValueError("微博 mid 不能为负数")
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if number == 0:
            return "0"
        encoded = ""
        while number:
            number, remainder = divmod(number, 62)
            encoded = alphabet[remainder] + encoded
        return encoded

    @classmethod
    def _mid_to_bid(cls, mid: str) -> str:
        """把十进制微博 mid 按七位分块转换为 base62 短 ID。"""
        if not mid.isdigit():
            raise ValueError("微博 mid 格式无效")
        reversed_mid = mid[::-1]
        chunks = []
        for offset in range(0, len(reversed_mid), 7):
            decimal_chunk = reversed_mid[offset : offset + 7][::-1]
            encoded = cls._base62_encode(int(decimal_chunk))
            if offset + 7 < len(reversed_mid):
                encoded = encoded.zfill(4)
            chunks.append(encoded)
        return "".join(reversed(chunks))

    _normalize_url = staticmethod(normalize_url)

    @staticmethod
    def _strip_html(value: object) -> str:
        if not isinstance(value, str):
            return ""
        text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
        text = re.sub(r"</(?:p|div|li|blockquote)\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).replace("\u200b", "").replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @classmethod
    def _is_auth_url(cls, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in cls.AUTH_PATH_MARKERS)

    def _raise_for_payload_cookie_error(self, payload: object) -> None:
        """识别微博业务载荷明确返回的登录和鉴权错误。"""
        if not isinstance(payload, dict):
            return
        message = str(
            payload.get("msg") or payload.get("message") or payload.get("errmsg") or ""
        ).lower()
        if any(
            marker in message
            for marker in ("login", "cookie", "未登录", "请登录", "权限")
        ):
            raise self.cookie_access_error()
