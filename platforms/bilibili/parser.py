import re
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import httpx

from ...core.contracts import OrderedContent, ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value
from ...core.parser import BaseParser


def _original_image_url(url: str) -> str:
    """返回哔哩哔哩托管图片的原图 URL。

    参数:
        url: 图片 URL，可以是省略协议的相对协议 URL。

    返回:
        移除哔哩哔哩转换后缀后的绝对 URL；非 ``hdslb.com`` 托管的 URL 原样返回。
    """
    normalized_url = f"https:{url}" if url.startswith("//") else url
    try:
        parsed_url = urlsplit(normalized_url)
        hostname = parsed_url.hostname or ""
        _ = parsed_url.port  # 触发 urllib 对端口的延迟校验。
    except ValueError:
        return url
    if hostname == "hdslb.com" or hostname.endswith(".hdslb.com"):
        transform_pattern = r"@(?:\d+w(?:_[^/]*)?|!web-[^/]+)(?:\.[^/]*)?$"
        path = re.sub(transform_pattern, "", parsed_url.path, count=1)
        return urlunsplit(parsed_url._replace(path=path))
    return normalized_url


class _ArticleHTMLParser(HTMLParser):
    """按文档顺序提取专栏中可见的文本和图片。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.contents: list[OrderedContent] = []
        self._article_depth = 0
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attributes = dict(attrs)
        if tag == "meta":
            if attributes.get("property") == "og:title":
                self.title = str(attributes.get("content") or "")
            elif attributes.get("name") == "author":
                self.author = str(attributes.get("content") or "")

        classes = str(attributes.get("class") or "").split()
        if (
            not self._article_depth
            and tag == "div"
            and {
                "article-holder",
                "article-content",
            }.intersection(classes)
        ):
            self._article_depth = 1
            return
        if not self._article_depth:
            return
        if tag == "div":
            self._article_depth += 1
        if tag in {"p", "h1", "h2", "h3", "li", "blockquote", "br"}:
            self._flush_text()
        if tag == "img":
            self._flush_text()
            image_url = str(attributes.get("data-src") or attributes.get("src") or "")
            image_url = _original_image_url(image_url)
            if image_url.startswith(("http://", "https://")):
                self.contents.append(OrderedContent(kind="image", value=image_url))

    def handle_endtag(self, tag: str):
        if not self._article_depth:
            return
        if tag in {"p", "h1", "h2", "h3", "li", "blockquote", "figure"}:
            self._flush_text()
        if tag == "div":
            self._article_depth -= 1
            if not self._article_depth:
                self._flush_text()

    def handle_data(self, data: str):
        if self._article_depth and (text := data.strip()):
            self._text_parts.append(text)

    def _flush_text(self):
        text = "".join(self._text_parts).strip()
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))


class BilibiliParser(BaseParser):
    name = "bilibili"
    display_name = "B站"
    cookie_config_key = "bilibili_cookies"
    cookie_failure_status_codes = frozenset({401, 403, 412})
    image_host_suffixes = ("hdslb.com",)
    SHORT_PATTERN = r"https?://(?:bili2233\.cn|b23\.tv)/[a-zA-Z0-9]+"
    ID_PATTERN = r"(BV[0-9A-Za-z]{10}|av\d+)"
    DYNAMIC_PATTERN = (
        r"https?://(?:t\.bilibili\.com/|www\.bilibili\.com/dynamic/)"
        r"(?P<dynamic_id>\d+)"
    )
    OPUS_PATTERN = r"https?://www\.bilibili\.com/opus/(?P<opus_id>\d+)"
    ARTICLE_PATTERN = r"https?://www\.bilibili\.com/read/cv(?P<article_id>\d+)"
    LIVE_PATTERN = r"https?://live\.bilibili\.com/(?P<room_id>\d+)"
    DYNAMIC_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/detail"
    OPUS_API = "https://api.bilibili.com/x/polymer/web-dynamic/v1/opus/detail"
    ARTICLE_API = "https://api.bilibili.com/x/article/view"
    LIVE_API = "https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom"
    COOKIE_FAILURE_CODES = {-101, -111, -352, -412}

    async def match(self, context: ParseContext) -> bool:
        text = context.combined_text
        return any(
            re.search(pattern, text)
            for pattern in (
                self.DYNAMIC_PATTERN,
                self.OPUS_PATTERN,
                self.ARTICLE_PATTERN,
                self.LIVE_PATTERN,
                self.ID_PATTERN,
                self.SHORT_PATTERN,
            )
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        text = context.combined_text
        if match := re.search(self.DYNAMIC_PATTERN, text):
            return await self._parse_dynamic(match.group("dynamic_id"))
        if match := re.search(self.OPUS_PATTERN, text):
            return await self._parse_opus(match.group("opus_id"))
        if match := re.search(self.ARTICLE_PATTERN, text):
            return await self._parse_article(match.group("article_id"))
        if match := re.search(self.LIVE_PATTERN, text):
            return await self._parse_live(match.group("room_id"))

        match = re.search(self.ID_PATTERN, text)
        if not match and (short_match := re.search(self.SHORT_PATTERN, text)):
            headers = self._headers("https://www.bilibili.com")
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(short_match.group(0), headers=headers)
            final_url = str(response.url)
            if final_url == short_match.group(0):
                self.raise_for_response_status(response)
                return ParseResult(platform=self.name, error="B站短链未发生跳转。")
            return await self.parse(ParseContext(text=final_url))

        video_id = match.group(0) if match else ""
        if not video_id:
            return ParseResult(platform=self.name, error="未找到 B站 视频 ID。")

        info = await self._get_video_info(video_id)
        if info.get("error"):
            return ParseResult(platform=self.name, error=info["error"])

        play_url = await self._get_play_url(str(info["cid"]), video_id)
        extra_lines = [] if play_url else ["无法获取视频直链。"]

        result = ParseResult(
            platform=self.name,
            title=info.get("title", "未知标题"),
            author=info.get("author", "未知作者"),
            description=info.get("desc", ""),
            cover_urls=[_original_image_url(str(info.get("pic", "")))],
            video_url=play_url,
            extra_lines=extra_lines,
        )
        referer = "https://www.bilibili.com"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
        ) as client:
            return await self.materialize_images(result, client, referer)

    async def _parse_live(self, room_id: str) -> ParseResult:
        """请求并解析 B站直播间公开信息。"""
        referer = f"https://live.bilibili.com/{room_id}"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
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
            anchor_info.get("base_info")
            if isinstance(anchor_info, dict)
            else {}
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
        cover_url = _original_image_url(str(room_info.get("keyframe") or ""))
        return ParseResult(
            platform=self.name,
            title=str(room_info.get("title") or "B站直播间"),
            author=str(base_info.get("uname") or "未知主播"),
            description=str(room_info.get("description") or ""),
            cover_urls=[cover_url] if cover_url else [],
            extra_lines=extra_lines,
        )

    async def _parse_dynamic(self, dynamic_id: str) -> ParseResult:
        referer = "https://www.bilibili.com"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(referer),
            cookies=self._cookies(),
        ) as client:
            response = await client.get(
                self.DYNAMIC_API,
                params={"id": dynamic_id},
            )
            self.raise_for_response_status(response)
            result = self._parse_dynamic_payload(response.json())
            return await self.materialize_images(result, client, referer)

    def _parse_dynamic_payload(self, payload: dict) -> ParseResult:
        """将哔哩哔哩动态载荷转换为有序解析结果。

        参数:
            payload: 解码后的动态详情响应。

        返回:
            解析后的动态元数据和有序内容。

        异常:
            ValueError: 响应中不包含动态条目时抛出。
        """
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
        # 动态主内容可能是图文或视频稿件，两类载荷的标题和图片字段不同。
        if major_type == "MAJOR_TYPE_OPUS":
            opus = major.get("opus") or {}
            title = str(opus.get("title") or title)
            image_urls = [
                _original_image_url(str(pic.get("url")))
                for pic in opus.get("pics") or []
                if pic and pic.get("url")
            ]
        elif major_type == "MAJOR_TYPE_ARCHIVE":
            archive = major.get("archive") or {}
            title = str(archive.get("title") or title)
            description = description or str(archive.get("desc") or "").strip()
            if cover := archive.get("cover"):
                image_urls.append(_original_image_url(str(cover)))
        elif major_type == "MAJOR_TYPE_ARTICLE":
            article = major.get("article") or {}
            title = str(article.get("title") or title)
            description = description or str(article.get("desc") or "").strip()
            image_urls = [
                _original_image_url(str(cover))
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
        ) as client:
            response = await client.get(
                self.OPUS_API,
                params={"id": opus_id},
            )
            self.raise_for_response_status(response)
            payload = response.json()
            data = payload.get("data") or {}
            if data.get("fallback"):
                response = await client.get(
                    self.DYNAMIC_API,
                    params={"id": opus_id},
                )
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
                        self.ARTICLE_API,
                        params={"id": article_id},
                    )
                    self.raise_for_response_status(response)
                    result = self._parse_article_payload(response.json())
                else:
                    result = self._parse_dynamic_payload(dynamic_payload)
            else:
                result = self._parse_opus_payload(payload)
            return await self.materialize_images(result, client, referer)

    def _parse_opus_payload(self, payload: dict) -> ParseResult:
        """将哔哩哔哩图文载荷转换为有序解析结果。

        参数:
            payload: 解码后的图文详情响应。

        返回:
            解析后的图文元数据和有序内容。

        异常:
            ValueError: 响应中不包含图文条目时抛出。
        """
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
        # 按模块和段落原始顺序追加文本、图片，确保最终消息保持页面图文顺序。
        for module in item.get("modules") or []:
            if not module:
                continue
            if module.get("module_author"):
                author = str(module["module_author"].get("name") or author)
            top = module.get("module_top") or {}
            display = top.get("display") or {}
            album = display.get("album") or {}
            for pic in album.get("pics") or []:
                if pic and (image_url := pic.get("url")):
                    ordered_contents.append(
                        OrderedContent(
                            kind="image",
                            value=_original_image_url(str(image_url)),
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
                        word = node.get("word") or {}
                        text_parts.append(str(word.get("words") or ""))
                    elif node.get("type") == "TEXT_NODE_TYPE_RICH":
                        rich = node.get("rich") or {}
                        text_parts.append(
                            str(rich.get("text") or rich.get("orig_text") or "")
                        )
                if text := "".join(text_parts).strip():
                    ordered_contents.append(OrderedContent(kind="text", value=text))
                picture = paragraph.get("pic") or {}
                for pic in picture.get("pics") or []:
                    if pic and (image_url := pic.get("url")):
                        ordered_contents.append(
                            OrderedContent(
                                kind="image",
                                value=_original_image_url(str(image_url)),
                            )
                        )
        return ParseResult(
            platform=self.name,
            title=title,
            author=author,
            ordered_contents=ordered_contents,
        )

    def _parse_article_payload(self, payload: dict) -> ParseResult:
        """将传统专栏接口载荷转换为包含完整正文的解析结果。"""
        self._raise_for_api_cookie_error(payload)
        if payload.get("code") not in (None, 0):
            raise ValueError(str(payload.get("message") or "B站专栏请求失败"))
        data = payload.get("data") or {}
        content = str(data.get("content") or "")
        if not content:
            raise ValueError("B站专栏正文不可访问")

        parsed = self._parse_article_html(
            f'<div class="article-content">{content}</div>'
        )
        author = data.get("author") or {}
        cover_candidates = data.get("origin_image_urls") or data.get("image_urls") or []
        if not isinstance(cover_candidates, list):
            cover_candidates = []
        cover_urls = []
        for cover in cover_candidates:
            cover_url = _original_image_url(str(cover or ""))
            if cover_url.startswith(("http://", "https://")) and cover_url not in cover_urls:
                cover_urls.append(cover_url)
        ordered_contents = [
            OrderedContent(kind="image", value=cover_url) for cover_url in cover_urls
        ]
        ordered_contents.extend(parsed.ordered_contents)
        return ParseResult(
            platform=self.name,
            title=str(data.get("title") or parsed.title),
            author=str(author.get("name") or parsed.author),
            ordered_contents=ordered_contents,
        )

    async def _parse_article(self, article_id: str) -> ParseResult:
        url = f"https://www.bilibili.com/read/cv{article_id}"
        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            headers=self._headers(url),
            cookies=self._cookies(),
        ) as client:
            response = await client.get(
                self.ARTICLE_API,
                params={"id": article_id},
            )
            self.raise_for_response_status(response)
            result = self._parse_article_payload(response.json())
            return await self.materialize_images(result, client, url)

    def _parse_article_html(self, html: str) -> ParseResult:
        """在不执行页面脚本的情况下提取公开专栏 HTML。

        参数:
            html: 哔哩哔哩专栏页面 HTML。

        返回:
            解析后的标题、作者和有序正文内容。

        异常:
            ValueError: 无法获取公开专栏正文时抛出。
        """
        parser = _ArticleHTMLParser()
        parser.feed(html)
        parser.close()
        if not parser.contents:
            raise ValueError("B站专栏正文不可访问")
        return ParseResult(
            platform=self.name,
            title=parser.title or "B站专栏",
            author=parser.author or "未知作者",
            ordered_contents=parser.contents,
        )

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
            api_url = f"https://api.bilibili.com/x/player/playurl?bvid={video_id}&cid={cid}&qn=16&type=mp4&platform=html5"
        elif id_type == "aid":
            api_url = f"https://api.bilibili.com/x/player/playurl?avid={video_id[2:]}&cid={cid}&qn=16&type=mp4&platform=html5"
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

    @staticmethod
    def _headers(referer: str) -> dict:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:102.0) Gecko/20100101 Firefox/102.0",
            "Referer": referer,
        }

    def _cookies(self) -> httpx.Cookies:
        """构造仅作用于哔哩哔哩域名的 Cookie Jar。"""
        return build_cookies(
            cookie_config_value(self.config, "bilibili_cookies"),
            (".bilibili.com",),
        )

    def _raise_for_api_cookie_error(self, payload: object) -> None:
        """识别 B站业务响应中的未登录、鉴权失败和风控错误码。"""
        code = payload.get("code") if isinstance(payload, dict) else None
        if code in self.COOKIE_FAILURE_CODES:
            raise self.cookie_access_error()
