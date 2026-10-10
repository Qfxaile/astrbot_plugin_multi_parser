from ...core.contracts import ParseResult


class DouyinGalleryContent:
    """解析抖音图集作品。"""

    SLIDES_URL = "https://www.iesdouyin.com/web/api/v2/aweme/slidesinfo/"

    def _parse_gallery_item(
        self,
        item: dict,
        title: str,
        author: str,
    ) -> ParseResult | None:
        """构建分享页中的图集结果；作品不是图集时返回空。"""
        images = item.get("images") or []
        if not isinstance(images, list):
            images = []
        if animated_result := self._parse_single_animated_image(images, title, author):
            return animated_result
        image_urls = []
        for image in images:
            image_url = self._select_image_url(image)
            if image_url:
                image_urls.append(image_url)
        if not image_urls:
            return None
        result = ParseResult(platform=self.name)
        result.content.title, result.content.author = title, author
        result.content.image_urls.extend(image_urls)
        return result

    def _parse_slides_data(self, data: dict) -> ParseResult:
        details = data.get("aweme_details") if isinstance(data, dict) else []
        if not isinstance(details, list):
            details = []
        item = next((value for value in details if isinstance(value, dict)), None)
        if item is None:
            raise ValueError("抖音 Slides 数据为空")
        images = item.get("images") or []
        if not isinstance(images, list):
            images = []
        title = str(item.get("desc") or "未知标题")
        author = str(
            item["author"].get("nickname") or "未知作者"
            if isinstance(item.get("author"), dict)
            else "未知作者"
        )
        if animated_result := self._parse_single_animated_image(images, title, author):
            return animated_result
        image_urls = []
        for image in images:
            image_url = self._select_image_url(image)
            if image_url:
                image_urls.append(image_url)
        if not image_urls:
            raise ValueError("抖音 Slides 中未找到图片")
        result = ParseResult(platform=self.name)
        result.content.title, result.content.author = title, author
        result.content.image_urls.extend(image_urls)
        return result

    def _parse_single_animated_image(
        self,
        images: list,
        title: str,
        author: str,
    ) -> ParseResult | None:
        """将单张实况图片转换为预览图与视频组合。"""
        if len(images) != 1 or not isinstance(images[0], dict):
            return None
        image = images[0]
        video_url, play_token = self._extract_video_source(image.get("video"))
        if not video_url and not play_token:
            return None
        cover_url = self._select_image_url(image)
        result = ParseResult(platform=self.name)
        result.content.title, result.content.author = title, author
        if cover_url:
            result.content.cover_urls.append(cover_url)
        result.media.video_url = video_url
        if play_token:
            result.content.extra_lines.append(f"play_token={play_token}")
        return result
