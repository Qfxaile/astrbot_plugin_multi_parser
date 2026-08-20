import httpx

from ...core.contracts import OrderedContent, ParseResult
from .common import original_image_url


class BilibiliDynamicContent:
    """解析 B站动态与图文动态。"""

    DYNAMIC_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
    OPUS_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"

    async def _parse_dynamic(self, dynamic_id: str) -> ParseResult:
        referer = "https://www.bilibili.com"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
            **self.http_client_options,
        ) as client:
            response = await client.get(self.DYNAMIC_API, params={"id": dynamic_id})
            self.raise_for_response_status(response)
            result = self._parse_dynamic_payload(response.json())
            return await self.materialize_images(result, client, referer)

    def _parse_dynamic_payload(self, payload: dict) -> ParseResult:
        """将 B站动态载荷转换为有序解析结果。"""
        self._raise_for_api_cookie_error(payload)
        if payload.get("code") not in (None, 0):
            raise ValueError(str(payload.get("message") or "B站动态请求失败"))
        item = (payload.get("data") or {}).get("item") or {}
        modules = item.get("modules") or {}
        if not modules:
            raise ValueError("B站动态数据为空")
        author_info = modules.get("module_author") or {}
        author = str(author_info.get("name") or "未知作者")
        dynamic = modules.get("module_dynamic") or {}
        description = str((dynamic.get("desc") or {}).get("text") or "").strip()
        major = dynamic.get("major") or {}
        major_type = major.get("type", "")
        title = "B站动态"
        image_urls: list[str] = []
        if major_type == "MAJOR_TYPE_OPUS":
            opus = major.get("opus") or {}
            title = str(opus.get("title") or title)
            image_urls = [
                original_image_url(str(pic.get("url")))
                for pic in opus.get("pics") or []
                if pic and pic.get("url")
            ]
        elif major_type == "MAJOR_TYPE_ARCHIVE":
            archive = major.get("archive") or {}
            title = str(archive.get("title") or title)
            description = description or str(archive.get("desc") or "").strip()
            if cover := archive.get("cover"):
                image_urls.append(original_image_url(str(cover)))
        elif major_type == "MAJOR_TYPE_ARTICLE":
            article = major.get("article") or {}
            title = str(article.get("title") or title)
            description = description or str(article.get("desc") or "").strip()
            image_urls = [
                original_image_url(str(cover))
                for cover in article.get("covers") or []
                if cover
            ]

        ordered_contents = []
        if description:
            ordered_contents.append(OrderedContent(kind="text", value=description))
        ordered_contents.extend(
            OrderedContent(kind="image", value=url) for url in image_urls
        )
        return ParseResult(
            platform=self.name,
            title=title,
            author=author,
            ordered_contents=ordered_contents,
        )

    async def _parse_opus(self, opus_id: str) -> ParseResult:
        referer = f"https://www.bilibili.com/opus/{opus_id}"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
            **self.http_client_options,
        ) as client:
            response = await client.get(self.OPUS_API, params={"id": opus_id})
            self.raise_for_response_status(response)
            payload = response.json()
            data = payload.get("data") or {}
            if data.get("fallback"):
                response = await client.get(self.DYNAMIC_API, params={"id": opus_id})
                self.raise_for_response_status(response)
                dynamic_payload = response.json()
                item = (dynamic_payload.get("data") or {}).get("item") or {}
                modules = item.get("modules") or {}
                dynamic = modules.get("module_dynamic") or {}
                major = dynamic.get("major") or {}
                article = major.get("article") or {}
                article_id = (
                    str(article.get("id") or "")
                    if major.get("type") == "MAJOR_TYPE_ARTICLE"
                    else ""
                )
                if article_id:
                    response = await client.get(
                        self.ARTICLE_API, params={"id": article_id}
                    )
                    self.raise_for_response_status(response)
                    result = self._parse_article_payload(response.json())
                else:
                    result = self._parse_dynamic_payload(dynamic_payload)
            else:
                result = self._parse_opus_payload(payload)
            return await self.materialize_images(result, client, referer)

    def _parse_opus_payload(self, payload: dict) -> ParseResult:
        """将 B站图文载荷转换为有序解析结果。"""
        self._raise_for_api_cookie_error(payload)
        if payload.get("code") not in (None, 0):
            raise ValueError(str(payload.get("message") or "B站图文请求失败"))
        item = (payload.get("data") or {}).get("item") or {}
        if not item:
            raise ValueError("B站图文数据为空")
        basic = item.get("basic") or {}
        title = str(basic.get("title") or "B站图文")
        author = "未知作者"
        ordered_contents: list[OrderedContent] = []
        for module in item.get("modules") or []:
            if not module:
                continue
            if module.get("module_author"):
                author = str(module["module_author"].get("name") or author)
            top = module.get("module_top") or {}
            album = (top.get("display") or {}).get("album") or {}
            for pic in album.get("pics") or []:
                if pic and (image_url := pic.get("url")):
                    ordered_contents.append(
                        OrderedContent(
                            kind="image",
                            value=original_image_url(str(image_url)),
                        )
                    )
            content = module.get("module_content") or {}
            for paragraph in content.get("paragraphs") or []:
                if not paragraph:
                    continue
                text_parts = []
                text = paragraph.get("text") or {}
                for node in text.get("nodes") or []:
                    if not node:
                        continue
                    if node.get("type") == "TEXT_NODE_TYPE_WORD":
                        text_parts.append(
                            str((node.get("word") or {}).get("words") or "")
                        )
                    elif node.get("type") == "TEXT_NODE_TYPE_RICH":
                        rich = node.get("rich") or {}
                        text_parts.append(
                            str(rich.get("text") or rich.get("orig_text") or "")
                        )
                if text_value := "".join(text_parts).strip():
                    ordered_contents.append(
                        OrderedContent(kind="text", value=text_value)
                    )
                picture = paragraph.get("pic") or {}
                for pic in picture.get("pics") or []:
                    if pic and (image_url := pic.get("url")):
                        ordered_contents.append(
                            OrderedContent(
                                kind="image",
                                value=original_image_url(str(image_url)),
                            )
                        )
        return ParseResult(
            platform=self.name,
            title=title,
            author=author,
            ordered_contents=ordered_contents,
        )
