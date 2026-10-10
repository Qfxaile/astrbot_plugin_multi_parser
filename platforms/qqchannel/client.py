"""腾讯频道公开详情请求客户端。"""

import json
import secrets
from collections.abc import Mapping

import httpx


async def request_feed(
    client: httpx.AsyncClient,
    detail_url: str,
    share_url: str,
    feed_id: str,
    *,
    guest_uin_min: int,
    guest_uin_max: int,
    max_response_bytes: int,
    raise_for_response,
    mapping,
) -> Mapping[str, object]:
    guest = str(guest_uin_min + secrets.randbelow(guest_uin_max - guest_uin_min + 1))
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Cookie": f"uuid={guest}; p_uin={guest}",
        "Referer": share_url,
        "X-Oidb": '{"uint32_command":"0x10f4","uint32_service_type":14}',
        "X-QQ-Client-AppId": "537246381",
    }
    body = {
        "feedId": feed_id,
        "from": 2,
        "detail_type": 1,
        "content_type": 2,
        "channelSign": {},
        "extInfo": {
            "mapInfo": [
                {"key": "qc-tabid", "value": "ark"},
                {"key": "qc-pageid", "value": "pc"},
            ]
        },
    }
    async with client.stream(
        "POST", detail_url, headers=headers, json=body
    ) as response:
        raise_for_response(response)
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > max_response_bytes:
            raise ValueError("帖子详情响应超过安全限制")
        chunks: list[bytes] = []
        received = 0
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            received += len(chunk)
            if received > max_response_bytes:
                raise ValueError("帖子详情响应超过安全限制")
            chunks.append(chunk)
    payload = json.loads(b"".join(chunks))
    if not isinstance(payload, Mapping) or payload.get("retcode") != 0:
        raise ValueError("帖子详情接口返回失败")
    feed = mapping(mapping(payload.get("data")).get("feed"))
    if not feed:
        raise ValueError("帖子详情为空")
    return feed
