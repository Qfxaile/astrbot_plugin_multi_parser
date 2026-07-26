from ...core.contracts import ParseResult


class RedBookNoteContent:
    """将小红书不同页面结构归一化为图集或视频笔记。"""

    def _parse_explore_state(self, state: dict, note_id: str) -> ParseResult:
        note_root = state.get("note") if isinstance(state, dict) else None
        note_map = (
            note_root.get("noteDetailMap") if isinstance(note_root, dict) else None
        )
        note_entry = note_map.get(note_id) if isinstance(note_map, dict) else None
        note = note_entry.get("note") if isinstance(note_entry, dict) else None
        if not isinstance(note, dict) or not note:
            raise ValueError("小红书 Explore 页面中未找到笔记数据")
        image_list = note.get("imageList")
        if not isinstance(image_list, list):
            image_list = []
        image_urls = [
            image_url
            for image in image_list
            if (
                image_url := self._select_original_image_url(
                    image, ("urlDefault", "url")
                )
            )
        ]
        video_url = (
            self._select_video_url(note.get("video"))
            if note.get("type") == "video"
            else ""
        )
        return ParseResult(
            platform=self.name,
            title=str(note.get("title") or "无标题"),
            author=str(
                note["user"].get("nickname") or "未知作者"
                if isinstance(note.get("user"), dict)
                else "未知作者"
            ),
            description=str(note.get("desc") or ""),
            cover_urls=image_urls[:1] if video_url else [],
            image_urls=[] if video_url else image_urls,
            video_url=video_url,
            extra_lines=[] if video_url or image_urls else ["未找到可发送的媒体。"],
        )

    def _parse_discovery_state(self, state: dict) -> ParseResult:
        note_data = state.get("noteData") if isinstance(state, dict) else None
        if not isinstance(note_data, dict):
            note_data = {}
        preload = note_data.get("normalNotePreloadData")
        if not isinstance(preload, dict):
            preload = {}
        data = note_data.get("data")
        note = data.get("noteData") if isinstance(data, dict) else None
        if not isinstance(note, dict) or not note:
            raise ValueError("小红书 Discovery 页面中未找到笔记数据")
        image_list = note.get("imageList")
        if not isinstance(image_list, list):
            image_list = []
        image_urls = [
            image_url
            for image in image_list
            if (
                image_url := self._select_original_image_url(
                    image, ("urlDefault", "url")
                )
            )
        ]
        video_url = (
            self._select_video_url(note.get("video"))
            if note.get("type") == "video"
            else ""
        )
        cover_urls = []
        if video_url:
            preload_images = preload.get("imagesList")
            if not isinstance(preload_images, list):
                preload_images = []
            cover_urls = [
                image_url
                for image in preload_images
                if (
                    image_url := self._select_original_image_url(
                        image, ("urlSizeLarge", "url")
                    )
                )
            ]
            cover_urls = cover_urls[:1] or image_urls[:1]
        return ParseResult(
            platform=self.name,
            title=str(note.get("title") or "无标题"),
            author=str(
                note["user"].get("nickName") or "未知作者"
                if isinstance(note.get("user"), dict)
                else "未知作者"
            ),
            description=str(note.get("desc") or ""),
            cover_urls=cover_urls,
            image_urls=[] if video_url else image_urls,
            video_url=video_url,
            extra_lines=[] if video_url or image_urls else ["未找到可发送的媒体。"],
        )
