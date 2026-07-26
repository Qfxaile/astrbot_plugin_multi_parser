from time import time

import httpx

from ...core.contracts import OrderedContent, ParseResult


class WeiboPostContent:
    """解析普通微博、转发微博及其中的混合媒体。"""

    async def _parse_status_id(self, status_id: str) -> ParseResult:
        referer = f"https://m.weibo.cn/detail/{status_id}"
        headers = {
            **self.HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Referer": referer,
            "Origin": "https://m.weibo.cn",
            "X-Requested-With": "XMLHttpRequest",
            "MWeibo-Pwa": "1",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=headers,
        ) as client:
            response = await client.get(
                "https://m.weibo.cn/statuses/show",
                params={"id": status_id, "_": int(time() * 1000)},
            )
            if response.status_code in {403, 418}:
                raise ValueError(f"微博接口被风控（{response.status_code}）")
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise ValueError("微博状态数据为空")
            result = self._parse_status_payload(data)
            return await self.materialize_images(result, client, referer)

    @classmethod
    def _select_video_url(cls, page_info: object) -> str:
        if not isinstance(page_info, dict):
            return ""
        urls = page_info.get("urls")
        if not isinstance(urls, dict):
            return ""
        for key in ("mp4_720p_mp4", "mp4_hd_mp4", "mp4_ld_mp4"):
            if url := cls._normalize_url(urls.get(key)):
                return url
        return ""

    @classmethod
    def _status_images(cls, status: dict) -> list[str]:
        pics = status.get("pics")
        if not isinstance(pics, list):
            return []
        image_urls = []
        for pic in pics:
            if not isinstance(pic, dict):
                continue
            large = pic.get("large")
            large_url = large.get("url") if isinstance(large, dict) else None
            if url := cls._normalize_url(large_url or pic.get("url")):
                image_urls.append(url)
        return image_urls

    @classmethod
    def _status_cover(cls, status: dict) -> str:
        page_info = status.get("page_info")
        if not isinstance(page_info, dict):
            return ""
        page_pic = page_info.get("page_pic")
        if not isinstance(page_pic, dict):
            return ""
        return cls._normalize_url(page_pic.get("url"))

    @classmethod
    def _append_status_content(
        cls,
        contents: list[OrderedContent],
        status: dict,
        text_prefix: str = "",
    ) -> None:
        text = cls._strip_html(status.get("text"))
        if text_prefix or text:
            value = "\n".join(part for part in (text_prefix, text) if part)
            if value:
                contents.append(OrderedContent(kind="text", value=value))
        contents.extend(
            OrderedContent(kind="image", value=url)
            for url in cls._status_images(status)
        )

    @classmethod
    def _parse_status_payload(cls, payload: dict) -> ParseResult:
        if not isinstance(payload, dict):
            raise ValueError("微博状态数据为空")
        user = payload.get("user")
        if not isinstance(user, dict) or not user.get("screen_name"):
            raise ValueError("微博作者数据为空")

        page_info = payload.get("page_info")
        page_info = page_info if isinstance(page_info, dict) else {}
        title = str(page_info.get("title") or payload.get("status_title") or "微博")
        contents: list[OrderedContent] = []
        cls._append_status_content(contents, payload)

        video_url = cls._select_video_url(page_info)
        cover_url = cls._status_cover(payload)
        repost = payload.get("retweeted_status")
        if isinstance(repost, dict):
            repost_user = repost.get("user")
            repost_author = (
                str(repost_user.get("screen_name"))
                if isinstance(repost_user, dict) and repost_user.get("screen_name")
                else "未知作者"
            )
            cls._append_status_content(contents, repost, f"转发自 @{repost_author}")
            if not video_url:
                video_url = cls._select_video_url(repost.get("page_info"))
                cover_url = cls._status_cover(repost)

        return ParseResult(
            platform=cls.name,
            title=title,
            author=str(user["screen_name"]),
            cover_urls=[cover_url] if cover_url and video_url else [],
            video_url=video_url,
            ordered_contents=contents,
            extra_lines=[] if video_url or contents else ["未找到可发送的媒体。"],
        )
