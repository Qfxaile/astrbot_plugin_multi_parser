import httpx

from ...core.contracts import ParseResult
from .common import original_image_url


class BilibiliVideoContent:
    """解析 B站视频元数据与播放地址。"""

    async def _parse_video(self, video_id: str) -> ParseResult:
        info = await self._get_video_info(video_id)
        if info.get("error"):
            return ParseResult(platform=self.name, error=info["error"])

        play_url = await self._get_play_url(str(info["cid"]), video_id)
        referer = "https://www.bilibili.com"
        result = ParseResult(
            platform=self.name,
            title=info.get("title", "未知标题"),
            author=info.get("author", "未知作者"),
            description=info.get("desc", ""),
            cover_urls=[original_image_url(str(info.get("pic", "")))],
            video_url=play_url,
            extra_lines=[] if play_url else ["无法获取视频直链。"],
            video_download_headers=self._headers(referer),
            video_download_host_suffixes=("bilivideo.com",),
        )
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
        ) as client:
            return await self.materialize_images(result, client, referer)

    @staticmethod
    def _id_type(video_id: str) -> str:
        return (
            "bvid"
            if video_id.startswith("BV")
            else "aid"
            if video_id.startswith("av")
            else "unknown"
        )

    async def _get_video_info(self, video_id: str) -> dict:
        id_type = self._id_type(video_id)
        if id_type == "bvid":
            api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
        elif id_type == "aid":
            api_url = (
                f"https://api.bilibili.com/x/web-interface/view?aid={video_id[2:]}"
            )
        else:
            return {"error": "未知ID类型"}

        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            cookies=self._cookies(),
        ) as client:
            response = await client.get(
                api_url, headers=self._headers("https://www.bilibili.com")
            )
            self.raise_for_response_status(response)
            data = response.json()
        self._raise_for_api_cookie_error(data)
        if data.get("code") != 0:
            return {"error": f"获取视频信息失败: {data.get('message')}"}

        video_data = data["data"]
        return {
            "title": video_data.get("title", "未知标题"),
            "pic": video_data.get("pic", ""),
            "author": video_data.get("owner", {}).get("name", "未知作者"),
            "desc": video_data.get("desc", ""),
            "cid": video_data.get("cid"),
        }

    async def _get_play_url(self, cid: str, video_id: str) -> str:
        id_type = self._id_type(video_id)
        if id_type == "bvid":
            api_url = (
                "https://api.bilibili.com/x/player/playurl"
                f"?bvid={video_id}&cid={cid}&qn=16&type=mp4&platform=html5"
            )
        elif id_type == "aid":
            api_url = (
                "https://api.bilibili.com/x/player/playurl"
                f"?avid={video_id[2:]}&cid={cid}&qn=16&type=mp4&platform=html5"
            )
        else:
            return ""

        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            cookies=self._cookies(),
        ) as client:
            response = await client.get(
                api_url, headers=self._headers("https://www.bilibili.com")
            )
            self.raise_for_response_status(response)
            data = response.json()
        self._raise_for_api_cookie_error(data)
        return (
            data.get("data", {}).get("durl", [{}])[0].get("url", "")
            if data.get("code") == 0
            else ""
        )
