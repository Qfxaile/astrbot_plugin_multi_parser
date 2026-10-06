"""Pixiv 官方 Ajax 请求客户端。"""

from collections.abc import Mapping

import httpx


async def request_body(
    client: httpx.AsyncClient, url: str, *, raise_for_response
) -> object:
    response = await client.get(url)
    raise_for_response(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Pixiv返回了无法读取的作品数据。") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("error")
        or payload.get("body") is None
    ):
        raise ValueError("Pixiv作品不可访问，可能已删除或受到访问限制。")
    return payload["body"]
