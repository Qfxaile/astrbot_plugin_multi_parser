"""通过腾讯官方接口解析微信视频号分享链接。"""

import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from httpx import Cookies

from ...core.contracts import ParseResult
from ...core.http import CookieAccessError, cookie_config_value, parse_cookie_header

YUANBAO_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
CHANNELS_FEED_INFO_URL = (
    "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def extract_token_and_export_id(url: str) -> tuple[str, str]:
    """从视频号预览长链中提取访问令牌和作品导出 ID。"""
    try:
        query = parse_qs(urlsplit(url).query)
    except ValueError:
        return "", ""
    token = str((query.get("token") or [""])[0])
    export_id = str((query.get("eid") or query.get("exportId") or [""])[0])
    return token, export_id


async def resolve_channels_share(
    client: httpx.AsyncClient,
    share_url: str,
    *,
    credentials_configured: bool,
    yuanbao_credentials: tuple[str, str] | None = None,
) -> ParseResult:
    """解析视频号分享链接并返回作品信息与可信视频直链。

    短链接先使用用户自己的腾讯元宝登录态换取 ``token`` 和 ``eid``；
    已携带这两个参数的浏览器长链可直接请求视频号预览接口。当前令牌
    只映射到元宝请求的认证头，兼容 Cookie 也仅由调用方绑定到元宝域。
    """
    token, export_id = extract_token_and_export_id(share_url)
    if not token or not export_id:
        token, export_id = await _exchange_share_url(
            client,
            share_url,
            credentials_configured=credentials_configured,
            yuanbao_credentials=yuanbao_credentials,
        )
    payload = await _get_feed_info(client, token, export_id)
    return parse_channels_payload(payload)


async def _exchange_share_url(
    client: httpx.AsyncClient,
    share_url: str,
    *,
    credentials_configured: bool,
    yuanbao_credentials: tuple[str, str] | None,
) -> tuple[str, str]:
    if not credentials_configured:
        raise CookieAccessError("微信视频号", configured=False)

    authentication_headers = {}
    if yuanbao_credentials is not None:
        user_id, token = yuanbao_credentials
        authentication_headers = {"X-ID": user_id, "X-Token": token}
    response = await client.post(
        YUANBAO_PARSE_URL,
        json={"type": "video_channel_url", "url": share_url, "scene": 1},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://yuanbao.tencent.com",
            "Referer": "https://yuanbao.tencent.com/chat",
            "User-Agent": _USER_AGENT,
            "X-Language": "zh-CN",
            "X-Platform": "mac",
            "X-Requested-With": "XMLHttpRequest",
            "X-Source": "web",
            **authentication_headers,
        },
    )
    if response.status_code in {401, 403}:
        raise CookieAccessError("微信视频号", configured=True)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("腾讯元宝返回了无法识别的解析数据。")
    if payload.get("code") not in (None, 0):
        message = str(payload.get("msg") or "腾讯元宝未能解析该视频号链接")
        raise ValueError(message)

    data = payload.get("data") or {}
    if not isinstance(data, Mapping):
        raise ValueError("腾讯元宝返回的视频号解析数据为空。")
    playable_url = str(data.get("playable_url") or "")
    token, export_id = extract_token_and_export_id(playable_url)
    export_id = export_id or str(data.get("wx_export_id") or "")
    if not token or not export_id:
        raise ValueError(
            "腾讯元宝未返回有效的视频号 token/eid，"
            "链接可能已失效或配置的 Cookies 已失效。"
        )
    return token, export_id


async def _get_feed_info(
    client: httpx.AsyncClient,
    token: str,
    export_id: str,
) -> dict[str, Any]:
    # rid 仅用于腾讯接口的请求追踪，不包含聊天内容或用户凭据。
    rid = f"{int(time.time()):x}-{secrets.token_hex(4)}"
    referer = (
        "https://channels.weixin.qq.com/finder-preview/pages/feed"
        f"?entry_card_type=48&comment_scene=39&appid=0&token={token}"
        f"&entry_scene=0&eid={export_id}"
    )
    response = await client.post(
        CHANNELS_FEED_INFO_URL,
        params={
            "_rid": rid,
            "_pageUrl": ("https://channels.weixin.qq.com/finder-preview/pages/feed"),
        },
        json={"baseReq": {"generalToken": token}, "exportId": export_id},
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://channels.weixin.qq.com",
            "Referer": referer,
            "User-Agent": _USER_AGENT,
        },
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, Mapping):
        raise ValueError("视频号预览接口返回了无法识别的数据。")
    if payload.get("errCode") not in (None, 0):
        message = payload.get("errMsg")
        if isinstance(message, Mapping):
            message = message.get("title") or message.get("message")
        raise ValueError(f"视频号预览接口返回错误: {message or '未知错误'}")
    return dict(payload)


def parse_channels_payload(payload: Mapping[str, Any]) -> ParseResult:
    """将视频号预览接口载荷转换为统一解析结果。"""
    data = payload.get("data") or {}
    if not isinstance(data, Mapping):
        raise ValueError("视频号预览数据为空。")
    feed_info = data.get("feedInfo") or {}
    author_info = data.get("authorInfo") or {}
    if not isinstance(feed_info, Mapping) or not feed_info:
        raise ValueError("视频号预览数据未包含作品信息。")
    if not isinstance(author_info, Mapping):
        author_info = {}

    error = feed_info.get("errMsg") or {}
    if isinstance(error, Mapping) and error.get("type"):
        raise ValueError(str(error.get("title") or "该视频号内容无法解析。"))

    video_url = _pick_video_url(feed_info)
    if not video_url:
        raise ValueError("视频号作品未返回可用视频直链，内容可能已删除或不是视频。")
    video_url = _validate_video_url(video_url)

    cover_url = str(feed_info.get("coverUrl") or "").strip()
    return ParseResult(
        platform="wechat",
        title=str(feed_info.get("description") or "微信视频号"),
        author=str(author_info.get("nickname") or "视频号用户"),
        cover_urls=[cover_url] if cover_url else [],
        video_url=video_url,
    )


def _pick_video_url(feed_info: Mapping[str, Any]) -> str:
    candidates: list[object] = []
    for key in ("h264VideoInfo", "h265VideoInfo"):
        info = feed_info.get(key)
        if isinstance(info, Mapping):
            candidates.append(info.get("videoUrl"))
    candidates.extend([feed_info.get("originVideoUrl"), feed_info.get("videoUrl")])
    return next(
        (
            candidate.strip()
            for candidate in candidates
            if isinstance(candidate, str) and candidate.strip()
        ),
        "",
    )


def _validate_video_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("视频号返回了格式错误的视频地址。") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "finder.video.qq.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ValueError("视频号返回了不受信任的视频地址。")
    return url


class WeChatChannelsContent:
    """请求并解析微信视频号分享内容。"""

    async def _parse_channels(self, url: str) -> ParseResult:
        cookie_value = cookie_config_value(self.config, self.cookie_config_key)
        configured_values = dict(parse_cookie_header(cookie_value))
        user_id = configured_values.pop("yb_user_id", "")
        token = configured_values.pop("yb_token", "")
        yuanbao_credentials = (user_id, token) if user_id and token else None
        cookies = Cookies()
        for name, value in configured_values.items():
            cookies.set(name, value, domain="yuanbao.tencent.com", path="/")
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            cookies=cookies,
            **self.http_client_options,
        ) as client:
            result = await resolve_channels_share(
                client,
                url,
                credentials_configured=(
                    yuanbao_credentials is not None or bool(configured_values)
                ),
                yuanbao_credentials=yuanbao_credentials,
            )
            return await self.materialize_images(
                result,
                client,
                "https://channels.weixin.qq.com/",
            )
