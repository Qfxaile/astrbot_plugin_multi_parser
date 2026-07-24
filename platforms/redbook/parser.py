import json
import re
from urllib.parse import quote, urlparse, urlsplit, urlunsplit

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value
from ...core.media import mark_invalid_legacy_images
from ...core.parser import BaseParser


class RedBookParser(BaseParser):
    name = "redbook"
    display_name = "小红书"
    cookie_config_key = "redbook_cookies"
    image_host_suffixes = ("xhscdn.com", "xiaohongshu.com")
    INVALID_IMAGE_URL = "unsafe-image-url"
    PATTERN = (
        r"https?://(?:"
        r"www\.xiaohongshu\.com/(?:explore|discovery/item)/[^/?\s]+"
        r"|xhslink\.com(?:/[^/?\s]+)+"
        r")(?:\?[^\s#]*)?"
    )
    NOTE_PATH_PATTERN = r"/(?:explore|discovery/item)/(?P<note_id>[^/?]+)"
    AUTH_PATH_MARKERS = ("/404/security-check", "/login", "/website-login")
    EXPLORE_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/55.0.2883.87 "
            "UBrowser/6.2.4098.3 Safari/537.36"
        )
    }
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Origin": "https://www.xiaohongshu.com",
    }

    async def match(self, context: ParseContext) -> bool:
        return bool(re.search(self.PATTERN, context.combined_text))

    async def parse(self, context: ParseContext) -> ParseResult:
        match = re.search(self.PATTERN, context.combined_text)
        if not match:
            return ParseResult(platform=self.name, error="未找到小红书链接。")

        cookies = build_cookies(
            cookie_config_value(self.config, "redbook_cookies"),
            (".xiaohongshu.com",),
        )

        async with httpx.AsyncClient(
            timeout=self.request_timeout,
            follow_redirects=True,
            headers=self.HEADERS,
            cookies=cookies,
        ) as client:
            url = match.group(0)
            if (urlparse(url).hostname or "") == "xhslink.com":
                response = await client.get(url, follow_redirects=False)
                if not response.has_redirect_location:
                    self.raise_for_response_status(response)
                    raise ValueError("小红书短链未返回重定向地址")
                url = str(response.url.join(response.headers["Location"]))
                if self._is_auth_url(url):
                    raise self.cookie_access_error()
                if (urlparse(url).hostname or "") != "www.xiaohongshu.com":
                    raise ValueError("小红书短链重定向到不受支持的地址")

            parsed_url = urlparse(url)
            note_match = re.search(self.NOTE_PATH_PATTERN, parsed_url.path)
            if not note_match:
                raise ValueError("无法从小红书链接中提取笔记 ID")
            note_id = note_match.group("note_id")
            query = f"?{parsed_url.query}" if parsed_url.query else ""
            explore_url = f"https://www.xiaohongshu.com/explore/{note_id}{query}"
            discovery_url = (
                f"https://www.xiaohongshu.com/discovery/item/{note_id}{query}"
            )

            # Explore 页面为首选数据源，访问失败或状态缺失时回退到 Discovery 页面。
            try:
                original_headers = client.headers.copy()
                client.headers.clear()
                client.headers.update(self.EXPLORE_HEADERS)
                try:
                    response = await client.get(explore_url)
                finally:
                    client.headers.clear()
                    client.headers.update(original_headers)
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                state = self._extract_initial_state(response.text)
                result = self._parse_explore_state(state, note_id)
                content_url = explore_url
            except (httpx.HTTPError, ValueError, KeyError):
                response = await client.get(discovery_url)
                self.raise_for_response_status(response)
                self._raise_for_auth_page(response)
                state = self._extract_initial_state(response.text)
                result = self._parse_discovery_state(state)
                content_url = discovery_url
            parsed_content_url = urlsplit(content_url)
            image_referer = urlunsplit(
                parsed_content_url._replace(query="", fragment="")
            )
            mark_invalid_legacy_images(result, self.INVALID_IMAGE_URL)
            # 页面解析结束后不再需要登录态；清空 Cookie，避免图片下载把
            # ``web_session`` 带到图片域或后续重定向目标。
            client.cookies.clear()
            return await self.materialize_images(result, client, image_referer)

    def _raise_for_auth_page(self, response: httpx.Response) -> None:
        """识别小红书跳转后的安全验证或登录页面。"""
        if self._is_auth_url(str(response.url)):
            raise self.cookie_access_error()

    @classmethod
    def _is_auth_url(cls, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in cls.AUTH_PATH_MARKERS)

    @staticmethod
    def _extract_initial_state(html: str) -> dict:
        matched = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(.*?)</script>",
            html,
            flags=re.DOTALL,
        )
        if not matched:
            raise ValueError("小红书分享链接失效或内容已删除")
        return json.loads(matched.group(1).strip().replace("undefined", "null"))

    def _parse_explore_state(self, state: dict, note_id: str) -> ParseResult:
        """将小红书 Explore 页面状态转换为统一解析结果。

        参数:
            state: 解码后的 ``window.__INITIAL_STATE__`` 对象。
            note_id: 用作映射键的笔记标识。

        返回:
            解析后的笔记元数据和媒体 URL。

        异常:
            ValueError: 页面状态中不存在目标笔记时抛出。
        """
        note_root = state.get("note") if isinstance(state, dict) else None
        note_map = (
            note_root.get("noteDetailMap") if isinstance(note_root, dict) else None
        )
        note_entry = note_map.get(note_id) if isinstance(note_map, dict) else None
        note = note_entry.get("note") if isinstance(note_entry, dict) else None
        if not isinstance(note, dict) or not note:
            raise ValueError("小红书 Explore 页面中未找到笔记数据")
        image_urls = []
        image_list = note.get("imageList")
        if not isinstance(image_list, list):
            image_list = []
        for image in image_list:
            image_url = self._select_original_image_url(image, ("urlDefault", "url"))
            if image_url:
                image_urls.append(image_url)
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
        """将小红书 Discovery 页面状态转换为统一解析结果。

        参数:
            state: 解码后的 ``window.__INITIAL_STATE__`` 对象。

        返回:
            解析后的笔记元数据和媒体 URL。

        异常:
            ValueError: 回退页面中不存在笔记数据时抛出。
        """
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
        image_urls = []
        image_list = note.get("imageList")
        if not isinstance(image_list, list):
            image_list = []
        for image in image_list:
            image_url = self._select_original_image_url(image, ("urlDefault", "url"))
            if image_url:
                image_urls.append(image_url)
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
            for image in preload_images:
                image_url = self._select_original_image_url(
                    image, ("urlSizeLarge", "url")
                )
                if image_url:
                    cover_urls.append(image_url)
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

    @staticmethod
    def _select_video_url(video: object) -> str:
        """从经过类型检查的外部数据中选择首选视频流。

        参数:
            video: 可能包含异常容器的外部视频元数据。

        返回:
            按编码优先级找到的第一个非空主 URL；没有有效变体时返回空字符串。
        """
        if not isinstance(video, dict):
            return ""
        media = video.get("media")
        if not isinstance(media, dict):
            return ""
        stream = media.get("stream")
        if not isinstance(stream, dict):
            return ""
        # 优先选择兼顾清晰度与常见客户端支持的视频编码。
        for codec in ("h265", "h264", "av1", "h266"):
            variants = stream.get(codec) or []
            if not isinstance(variants, list):
                continue
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                if isinstance(variant.get("masterUrl"), str) and variant["masterUrl"]:
                    return variant["masterUrl"]
        return ""

    @classmethod
    def _select_original_image_url(
        cls,
        image: object,
        fallback_fields: tuple[str, ...],
    ) -> str:
        """按优先级选择第一个安全的原图候选地址。

        参数:
            image: 可能包含异常值的外部图片元数据。
            fallback_fields: 在文件标识和链路标识之后依次检查的 URL 字段。

        返回:
            由固定 CDN 和图片标识组成的 URL、首个安全回退 URL、所有候选均不安全时
            可供本地拒绝的失败候选，或没有字符串候选时的空字符串。
        """
        if not isinstance(image, dict):
            return ""
        # 图片标识只作为路径并进行转义，网络位置部分固定为可信的小红书 CDN。
        for field_name in ("fileId", "traceId"):
            image_id = image.get(field_name)
            if isinstance(image_id, str) and image_id:
                path = f"/{quote(image_id.lstrip('/'), safe='/')}"
                return urlunsplit(("https", "sns-img-qc.xhscdn.com", path, "", ""))

        # 回退 URL 不改写网络位置部分；无效值保留为失败槽位，由统一下载流程生成提示。
        failure_candidate = ""
        for field_name in fallback_fields:
            candidate = image.get(field_name)
            if not isinstance(candidate, str) or not candidate:
                continue
            try:
                parsed = urlsplit(candidate)
                port = parsed.port
            except ValueError:
                failure_candidate = failure_candidate or candidate
                continue
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or port not in {None, 80, 443}
            ):
                failure_candidate = failure_candidate or cls.INVALID_IMAGE_URL
                continue
            return cls._strip_image_transform(candidate)
        return failure_candidate

    @staticmethod
    def _strip_image_transform(url: str) -> str:
        """在不改写网络位置部分的情况下规范化安全图片 URL。

        参数:
            url: 路径末尾可能包含 ``!<transform>`` 转换后缀的图片 URL。

        返回:
            仅移除转换后缀的 URL；可由 httpx 在本地拒绝的异常值保持原样；协议、凭据、
            主机或端口不安全时返回无效标记。
        """
        try:
            parsed = urlsplit(url)
        except ValueError:
            return url
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return RedBookParser.INVALID_IMAGE_URL
        try:
            port = parsed.port
        except ValueError:
            return url
        if port not in {None, 80, 443}:
            return RedBookParser.INVALID_IMAGE_URL
        path = re.sub(r"![^/]*$", "", parsed.path)
        return urlunsplit(parsed._replace(path=path))
