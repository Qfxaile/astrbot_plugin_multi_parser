"""受控读取可信网页的 HTML。"""

from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from .http import is_trusted_https_url

_REDIRECT_ERROR = "分享链接跳转到不可信域名。"
_MISSING_LOCATION_ERROR = "分享链接缺少跳转地址。"
_TOO_MANY_REDIRECTS_ERROR = "网页重定向次数超过安全限制。"
_NON_HTML_ERROR = "目标网页不是可读取的 HTML。"
_TOO_LARGE_ERROR = "目标网页响应过大，已停止解析。"
_READ_CHUNK_SIZE = 64 * 1024


class TrustedWebPageError(ValueError):
    """表示可信网页的跳转或响应未通过安全边界。"""


@dataclass(frozen=True)
class FetchedWebPage:
    """已验证来源并读取完成的 HTML 页面。"""

    final_url: str
    html: str


async def fetch_trusted_html(
    client: httpx.AsyncClient,
    url: str,
    host_suffixes: Collection[str],
    *,
    max_redirects: int = 5,
    max_bytes: int = 2 * 1024 * 1024,
) -> FetchedWebPage:
    """在可信 HTTPS 域内跟随跳转，并限制读取网页 HTML 的大小。"""
    current_url = url
    redirect_count = 0

    while True:
        if not is_trusted_https_url(current_url, host_suffixes):
            raise TrustedWebPageError(_REDIRECT_ERROR)

        async with client.stream(
            "GET", current_url, follow_redirects=False
        ) as response:
            if response.is_redirect:
                redirect_count += 1
                if redirect_count > max_redirects:
                    raise TrustedWebPageError(_TOO_MANY_REDIRECTS_ERROR)

                location = response.headers.get("Location")
                if not location:
                    raise TrustedWebPageError(_MISSING_LOCATION_ERROR)

                current_url = urljoin(current_url, location)
                if not is_trusted_https_url(current_url, host_suffixes):
                    raise TrustedWebPageError(_REDIRECT_ERROR)
                continue

            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "text/html":
                raise TrustedWebPageError(_NON_HTML_ERROR)

            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > max_bytes:
                    raise TrustedWebPageError(_TOO_LARGE_ERROR)

            chunks: list[bytes] = []
            received_size = 0
            async for chunk in response.aiter_bytes(chunk_size=_READ_CHUNK_SIZE):
                received_size += len(chunk)
                if received_size > max_bytes:
                    raise TrustedWebPageError(_TOO_LARGE_ERROR)
                chunks.append(chunk)

            content = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            try:
                html = content.decode(encoding, errors="replace")
            except LookupError:
                html = content.decode("utf-8", errors="replace")
            return FetchedWebPage(final_url=str(response.url), html=html)
