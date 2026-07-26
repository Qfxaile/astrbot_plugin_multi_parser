import re
from urllib.parse import urlsplit, urlunsplit

import httpx

from ...core.http import build_cookies, cookie_config_value


def original_image_url(url: str) -> str:
    """返回移除 B站图片转换后缀的原图 URL。"""
    normalized_url = f"https:{url}" if url.startswith("//") else url
    try:
        parsed_url = urlsplit(normalized_url)
        hostname = parsed_url.hostname or ""
        _ = parsed_url.port
    except ValueError:
        return url
    if hostname == "hdslb.com" or hostname.endswith(".hdslb.com"):
        transform_pattern = r"@(?:\d+w(?:_[^/]*)?|!web-[^/]+)(?:\.[^/]*)?$"
        path = re.sub(transform_pattern, "", parsed_url.path, count=1)
        return urlunsplit(parsed_url._replace(path=path))
    return normalized_url


class BilibiliContentSupport:
    """提供各类 B站内容共享的请求与鉴权能力。"""

    COOKIE_FAILURE_CODES = {-101, -111, -352, -412}

    @staticmethod
    def _headers(referer: str) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:102.0) "
                "Gecko/20100101 Firefox/102.0"
            ),
            "Referer": referer,
        }

    def _cookies(self) -> httpx.Cookies:
        """构造仅作用于哔哩哔哩域名的 Cookie Jar。"""
        return build_cookies(
            cookie_config_value(self.config, "bilibili_cookies"),
            (".bilibili.com",),
        )

    def _raise_for_api_cookie_error(self, payload: object) -> None:
        """识别 B站业务响应中的未登录、鉴权失败和风控错误码。"""
        code = payload.get("code") if isinstance(payload, dict) else None
        if code in self.COOKIE_FAILURE_CODES:
            raise self.cookie_access_error()
