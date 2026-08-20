import httpx

from ...core.contracts import ParseResult
from .common import original_image_url


class BilibiliLiveContent:
    """解析 B站直播间内容。"""

    LIVE_API = "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom"

    async def _parse_live(self, room_id: str) -> ParseResult:
        """请求并解析 B站直播间公开信息。"""
        referer = f"https://live.bilibili.com/{room_id}"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
            **self.http_client_options,
        ) as client:
            response = await client.get(self.LIVE_API, params={"room_id": room_id})
            self.raise_for_response_status(response)
            result = self._parse_live_payload(response.json())
            return await self.materialize_images(result, client, referer)

    def _parse_live_payload(self, payload: dict) -> ParseResult:
        """将 B站直播间载荷转换为统一解析结果。"""
        if not isinstance(payload, dict):
            raise ValueError("B站直播间响应格式错误")
        self._raise_for_api_cookie_error(payload)
        if payload.get("code") not in (None, 0):
            raise ValueError(str(payload.get("message") or "B站直播间请求失败"))
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("B站直播间数据为空")
        room_info = data.get("room_info") or {}
        if not isinstance(room_info, dict) or not room_info:
            raise ValueError("B站直播间数据为空")
        anchor_info = data.get("anchor_info") or {}
        base_info = (
            anchor_info.get("base_info") if isinstance(anchor_info, dict) else {}
        ) or {}
        if not isinstance(base_info, dict):
            base_info = {}
        live_status = {0: "未开播", 1: "直播中", 2: "轮播中"}.get(
            room_info.get("live_status"), "未知"
        )
        extra_lines = [f"直播状态: {live_status}"]
        areas = [
            str(room_info.get(key) or "").strip()
            for key in ("parent_area_name", "area_name")
        ]
        areas = list(dict.fromkeys(area for area in areas if area))
        if areas:
            extra_lines.append(f"分区: {' / '.join(areas)}")
        online = room_info.get("online")
        if isinstance(online, int) and online >= 0:
            extra_lines.append(f"人气: {online:,}")
        cover_url = original_image_url(str(room_info.get("keyframe") or ""))
        return ParseResult(
            platform=self.name,
            title=str(room_info.get("title") or "B站直播间"),
            author=str(base_info.get("uname") or "未知主播"),
            description=str(room_info.get("description") or ""),
            cover_urls=[cover_url] if cover_url else [],
            extra_lines=extra_lines,
        )
