"""微博分享链接跳转客户端。"""

import re

import httpx


async def resolve_share_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    patterns: tuple[str, ...],
    trusted_url,
    auth_url,
    raise_for_response,
) -> str:
    response = await client.get(url)
    raise_for_response(response)
    final_url = str(response.url)
    if final_url == url:
        raise ValueError("微博分享链接未发生跳转")
    if auth_url(final_url):
        raise ValueError("微博 Cookies 可能已失效，请更新后重试。")
    if not trusted_url(final_url) or not any(
        re.search(pattern, final_url) for pattern in patterns
    ):
        raise ValueError("微博分享链接跳转到不可信域名")
    return final_url
