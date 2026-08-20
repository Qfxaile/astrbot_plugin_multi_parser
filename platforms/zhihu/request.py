from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from ...core.http import (
    build_cookie_access_error,
    build_cookies,
    cookie_config_value,
    http_client_proxy_options,
    raise_for_cookie_access,
    request_timeout,
)


class ZhihuRequestError(ValueError):
    """知乎请求失败，消息不包含可能带令牌的完整 URL。"""


class ZhihuRequest:
    DISPLAY_NAME = "知乎"
    COOKIE_CONFIG_KEY = "zhihu_cookies"
    AUTH_PATH_MARKERS = ("/signin", "/account/unhuman", "/captcha")
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.zhihu.com/",
        "Origin": "https://www.zhihu.com",
    }

    def __init__(self, config):
        self.config = config

    def create_client(self):
        return httpx.AsyncClient(
            timeout=request_timeout(self.config),
            follow_redirects=True,
            headers=self.HEADERS,
            cookies=self._cookies(),
            **http_client_proxy_options(self.config, "zhihu"),
        )

    def _cookies(self) -> httpx.Cookies:
        return build_cookies(self._cookie_value(), (".zhihu.com",))

    def _cookie_value(self) -> object:
        """读取知乎 Cookie 原始配置，仅供核心 Cookie 工具消费。"""
        return cookie_config_value(self.config, self.COOKIE_CONFIG_KEY)

    async def get_json(self, client, url: str, *, params: dict | None = None) -> dict:
        response = await client.get(url, params=params)
        if response.status_code >= 400:
            raise ZhihuRequestError(f"知乎接口请求失败（{response.status_code}）")
        try:
            payload = response.json()
        except Exception as exc:
            raise ZhihuRequestError("知乎接口返回非 JSON 数据") from exc
        if not isinstance(payload, dict):
            raise ZhihuRequestError("知乎接口数据格式错误")
        return payload

    async def get_page(self, client, url: str) -> str:
        response = await client.get(
            url,
            headers={"Accept": "text/html,application/xhtml+xml,*/*"},
        )
        raise_for_cookie_access(
            response,
            platform=self.DISPLAY_NAME,
            cookie_value=self._cookie_value(),
        )
        if response.status_code >= 400:
            raise ZhihuRequestError(f"知乎页面请求失败（{response.status_code}）")
        self._raise_for_auth_page(response)
        return response.text

    async def expand_share(self, client, url: str) -> str:
        response = await client.get(url)
        raise_for_cookie_access(
            response,
            platform=self.DISPLAY_NAME,
            cookie_value=self._cookie_value(),
        )
        if response.status_code >= 400:
            raise ZhihuRequestError(f"知乎分享链接请求失败（{response.status_code}）")
        self._raise_for_auth_page(response)
        final_url = str(response.url)
        if final_url == url:
            raise ZhihuRequestError("知乎分享链接未发生跳转")
        return final_url

    @staticmethod
    def redact_error_message(value: str) -> str:
        return re.sub(r"https?://\S+", "[已隐藏 URL]", value)

    def _raise_for_auth_page(self, response: httpx.Response) -> None:
        """识别知乎跳转后的登录和人机验证页面。"""
        path = urlparse(str(response.url)).path.lower()
        if any(marker in path for marker in self.AUTH_PATH_MARKERS):
            raise build_cookie_access_error(
                self.DISPLAY_NAME,
                self._cookie_value(),
            )
