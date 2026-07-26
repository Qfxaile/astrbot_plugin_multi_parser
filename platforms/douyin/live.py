import json
import re

import httpx

from ...core.contracts import ParseResult
from ...core.media import mark_invalid_legacy_images


class DouyinLiveContent:
    """解析抖音直播间和直播回流页。"""

    LIVE_API = "https://live.douyin.com/webcast/room/web/enter/"
    LIVE_REFLOW_PATTERN = (
        r"https?://webcast\.amemv\.com/douyin/webcast/reflow/"
        r"(?P<room_id>\d+)"
    )

    def _parse_live_reflow_html(self, html: str) -> ParseResult:
        """从抖音 App 直播分享回流页提取公开直播间信息。"""
        room = self._extract_live_reflow_room(html)
        owner = room.get("owner") or {}
        if not isinstance(owner, dict):
            owner = {}
        cover = room.get("cover") or room.get("roomCover") or {}
        if isinstance(cover, dict) and "urlList" in cover:
            cover = {"url_list": cover.get("urlList")}
        cover_url = self._select_image_url(cover)
        live_status = {0: "未开播", 2: "直播中", 4: "已结束"}.get(
            room.get("status"), "未知"
        )
        extra_lines = [f"直播状态: {live_status}"]
        user_count = room.get("userCount")
        if isinstance(user_count, int) and user_count >= 0:
            extra_lines.append(f"观看人数: {user_count:,}")
        return ParseResult(
            platform=self.name,
            title=str(room.get("title") or "抖音直播间"),
            author=str(owner.get("nickname") or "未知主播"),
            cover_urls=[cover_url] if cover_url else [],
            extra_lines=extra_lines,
        )

    @classmethod
    def _extract_live_reflow_room(cls, html: str) -> dict:
        """解码 React 流式数据并返回其中的直播间对象。"""
        for matched in re.finditer(
            r"self\.__rsc_f\.push\((.*?)\)</script>",
            html,
            flags=re.DOTALL,
        ):
            try:
                chunk = json.loads(matched.group(1))
            except (json.JSONDecodeError, TypeError):
                continue
            if room := cls._find_live_reflow_room(chunk):
                return room
        raise ValueError("抖音直播回流页中未找到直播间数据")

    @classmethod
    def _find_live_reflow_room(cls, value: object) -> dict:
        """递归查找 RSC 数据块中的 ``data.room`` 对象。"""
        if isinstance(value, dict):
            data = value.get("data")
            if isinstance(data, dict) and isinstance(data.get("room"), dict):
                return data["room"]
            for nested in value.values():
                if room := cls._find_live_reflow_room(nested):
                    return room
        elif isinstance(value, list):
            for nested in value:
                if room := cls._find_live_reflow_room(nested):
                    return room
        elif isinstance(value, str):
            serialized = value.split(":", 1)[1] if ":" in value else value
            try:
                nested = json.loads(serialized)
            except json.JSONDecodeError:
                return {}
            return cls._find_live_reflow_room(nested)
        return {}

    async def _parse_live(
        self,
        client: httpx.AsyncClient,
        web_rid: str,
    ) -> ParseResult:
        """请求并解析抖音直播间公开信息。"""
        referer = f"https://live.douyin.com/{web_rid}"
        await self._ensure_ttwid(client)
        response = await client.get(
            self.LIVE_API,
            params={
                "aid": "6383",
                "app_name": "douyin_web",
                "device_platform": "web",
                "language": "zh-CN",
                "enter_from": "web_live",
                "cookie_enabled": "true",
                "browser_language": "zh-CN",
                "browser_platform": "Win32",
                "browser_name": "Safari",
                "browser_version": "17.0",
                "web_rid": web_rid,
            },
            headers={"Referer": referer},
        )
        self.raise_for_response_status(response)
        result = self._parse_live_data(response.json())
        mark_invalid_legacy_images(result, self.INVALID_IMAGE_URL)
        return await self.materialize_images(result, client, referer)

    def _parse_live_data(self, payload: dict) -> ParseResult:
        """将抖音直播间载荷转换为统一解析结果。"""
        if not isinstance(payload, dict):
            raise ValueError("抖音直播间响应格式错误")
        if payload.get("status_code") not in (None, 0):
            raise ValueError(str(payload.get("status_msg") or "抖音直播间请求失败"))
        data = payload.get("data") or {}
        rooms = data.get("data") if isinstance(data, dict) else data
        if not isinstance(rooms, list):
            rooms = []
        room = next((item for item in rooms if isinstance(item, dict)), None)
        if room is None:
            raise ValueError("抖音直播间数据为空")
        user = room.get("user") or room.get("owner") or {}
        if not isinstance(user, dict):
            user = {}
        cover_url = self._select_image_url(room.get("cover") or room.get("room_cover"))
        live_status = {0: "未开播", 2: "直播中", 4: "已结束"}.get(
            room.get("status"), "未知"
        )
        extra_lines = [f"直播状态: {live_status}"]
        view_stats = room.get("room_view_stats") or {}
        if isinstance(view_stats, dict):
            display_value = str(view_stats.get("display_value") or "").strip()
            if display_value:
                extra_lines.append(f"观看人数: {display_value}")
        return ParseResult(
            platform=self.name,
            title=str(room.get("title") or "抖音直播间"),
            author=str(user.get("nickname") or "未知主播"),
            cover_urls=[cover_url] if cover_url else [],
            extra_lines=extra_lines,
        )
