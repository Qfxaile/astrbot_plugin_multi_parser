import json

import httpx

from ...core.contracts import ParseResult


class WeiboVideoContent:
    """解析微博独立视频页。"""

    async def _parse_video_fid(self, fid: str) -> ParseResult:
        referer = f"https://h5.video.weibo.com/show/{fid}"
        headers = {
            **self.HEADERS,
            "Referer": referer,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=headers,
            cookies=self._cookies(),
        ) as client:
            response = await client.post(
                f"https://h5.video.weibo.com/api/component?page=/show/{fid}",
                content="data="
                + json.dumps(
                    {"Component_Play_Playinfo": {"oid": fid}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            self.raise_for_response_status(response)
            payload = response.json()
            self._raise_for_payload_cookie_error(payload)
            result = self._parse_video_payload(payload)
            return await self.materialize_images(result, client, referer)

    @classmethod
    def _parse_video_payload(cls, payload: object) -> ParseResult:
        data = payload.get("data") if isinstance(payload, dict) else None
        component = (
            data.get("Component_Play_Playinfo") if isinstance(data, dict) else None
        )
        if not isinstance(component, dict) or not component:
            raise ValueError("微博视频数据为空")
        reward = component.get("reward")
        user = reward.get("user") if isinstance(reward, dict) else None
        author = (
            str(user.get("name") or "未知作者")
            if isinstance(user, dict)
            else "未知作者"
        )
        urls = component.get("urls")
        video_url = ""
        if isinstance(urls, dict):
            video_url = next(
                (
                    normalized
                    for value in urls.values()
                    if (normalized := cls._normalize_url(value))
                ),
                "",
            )
        if not video_url:
            video_url = cls._normalize_url(component.get("stream_url"))
        cover_url = cls._normalize_url(component.get("cover_image"))
        return ParseResult(
            platform=cls.name,
            title=str(component.get("title") or "微博视频"),
            author=author,
            description=cls._strip_html(component.get("text")),
            cover_urls=[cover_url] if cover_url else [],
            video_url=video_url,
            extra_lines=[] if video_url else ["无法获取微博视频直链。"],
        )
