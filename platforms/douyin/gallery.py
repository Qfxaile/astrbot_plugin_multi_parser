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
        image_urls = []
        for image in images:
            image_url = self._select_image_url(image)
            if image_url:
                image_urls.append(image_url)
        if not image_urls:
            return None
        return ParseResult(
            platform=self.name,
            title=title,
            author=author,
            image_urls=image_urls,
        )

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
        image_urls = []
        for image in images:
            image_url = self._select_image_url(image)
            if image_url:
                image_urls.append(image_url)
        if not image_urls:
            raise ValueError("抖音 Slides 中未找到图片")
        return ParseResult(
            platform=self.name,
            title=str(item.get("desc") or "未知标题"),
            author=str(
                item["author"].get("nickname") or "未知作者"
                if isinstance(item.get("author"), dict)
                else "未知作者"
            ),
            image_urls=image_urls,
        )
