"""抖音链接跳转与受信任地址校验。"""

from urllib.parse import urljoin

import httpx

from ...core.http import is_trusted_https_url


class DouyinRedirectError(ValueError):
    """表示抖音分享短链未通过受信任跳转校验。"""


async def resolve_short_link(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_redirects: int,
    trusted_url,
    raise_for_response,
    raise_for_auth,
) -> httpx.Response:
    """在抖音可信域内逐跳解析分享短链。"""
    current_url = url
    redirect_count = 0
    while True:
        response = await client.get(current_url, follow_redirects=False)
        if not response.is_redirect:
            raise_for_response(response)
            raise_for_auth(response)
            return response
        redirect_count += 1
        if redirect_count > max_redirects:
            raise DouyinRedirectError("抖音分享链接重定向次数超过安全限制。")
        location = response.headers.get("Location")
        if not location:
            raise DouyinRedirectError("抖音分享链接缺少跳转地址。")
        target_url = urljoin(current_url, location)
        if not trusted_url(target_url):
            raise DouyinRedirectError("抖音分享链接跳转到不可信域名。")
        current_url = target_url


def trusted_redirect_url(
    url: str, *, redirect_suffixes: tuple[str, ...], is_shop_url
) -> bool:
    return is_trusted_https_url(url, redirect_suffixes) or is_shop_url(url)
