"""分派抖音分享页中的图集或视频作品。"""

import json
import re

from ...core.contracts import ParseResult


class DouyinWorkContent:
    """提取通用作品载荷并交给对应格式构建器。"""

    @staticmethod
    def _extract_router_data(html: str) -> dict:
        matched = re.search(
            r"window\._ROUTER_DATA\s*=\s*(.*?)</script>", html, flags=re.DOTALL
        )
        if not matched:
            raise ValueError("抖音分享页中未找到 _ROUTER_DATA")
        return json.loads(matched.group(1).strip())

    def _parse_router_data(self, data: dict) -> ParseResult:
        """提取作品通用字段并按实际媒体格式分派。"""
        loader_data = data.get("loaderData", {}) if isinstance(data, dict) else {}
        if not isinstance(loader_data, dict):
            loader_data = {}
        page = next(
            (
                value
                for key, value in loader_data.items()
                if isinstance(key, str)
                and isinstance(value, dict)
                and key.startswith(("video_", "note_"))
                and key.endswith("/page")
            ),
            None,
        )
        video_info = (page or {}).get("videoInfoRes", {})
        if not isinstance(video_info, dict):
            video_info = {}
        items = video_info.get("item_list", [])
        if not isinstance(items, list):
            items = []
        item = next((value for value in items if isinstance(value, dict)), None)
        if item is None:
            raise ValueError("抖音分享页中未找到作品数据")
        author_data = item.get("author")
        author = str(
            author_data.get("nickname")
            if isinstance(author_data, dict) and author_data.get("nickname")
            else "未知作者"
        )
        title = str(item.get("desc") or "未知标题")
        gallery_result = self._parse_gallery_item(item, title, author)
        return gallery_result or self._parse_video_item(item, title, author)
