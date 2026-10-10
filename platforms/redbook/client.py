"""小红书短链与页面地址客户端。"""

from urllib.parse import urlparse

import httpx


async def resolve_short_link(
    client: httpx.AsyncClient,
    url: str,
    *,
    supported_hosts: frozenset[str],
    raise_for_response,
    auth_url,
) -> str:
    response = await client.get(url, follow_redirects=False)
    if not response.has_redirect_location:
        raise_for_response(response)
        raise ValueError("小红书短链未返回重定向地址")
    target_url = str(response.url.join(response.headers["Location"]))
    if auth_url(target_url):
        raise ValueError("小红书 Cookies 可能已失效，请更新后重试。")
    if (urlparse(target_url).hostname or "").lower() not in supported_hosts:
        raise ValueError("小红书短链重定向到不受支持的地址")
    return target_url
