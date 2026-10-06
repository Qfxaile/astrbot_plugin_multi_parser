"""番茄小说分享链接跳转客户端。"""

from urllib.parse import urljoin

import httpx

from ...core.http import is_trusted_https_url
from ...core.webpage import TrustedWebPageError


async def resolve_book_id(
    client: httpx.AsyncClient,
    url: str,
    *,
    page_host_suffixes: tuple[str, ...],
    extract_book_id,
) -> str:
    """只读取可信跳转响应头，避免加载分享页动态内容。"""
    current_url = url
    for _ in range(6):
        if book_id := extract_book_id(current_url):
            return book_id
        if not is_trusted_https_url(current_url, page_host_suffixes):
            raise TrustedWebPageError("番茄小说分享链接跳转到不可信域名。")
        response = await client.get(current_url, follow_redirects=False)
        if not response.is_redirect:
            response.raise_for_status()
            return extract_book_id(str(response.url))
        location = response.headers.get("Location")
        if not location:
            raise TrustedWebPageError("番茄小说分享链接缺少跳转地址。")
        current_url = urljoin(current_url, location)
    raise TrustedWebPageError("番茄小说分享链接重定向次数超过安全限制。")
