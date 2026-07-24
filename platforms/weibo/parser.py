from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from time import time
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from ...core.contracts import OrderedContent, ParseContext, ParseResult
from ...core.http import build_cookies, cookie_config_value
from ...core.parser import BaseParser


class _WeiboArticleParser(HTMLParser):
    """按微博长文章中的可见顺序提取文本和图片。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contents: list[OrderedContent] = []
        self._text_parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "h1", "h2", "h3", "br"}:
            self._flush_text()
        if tag == "img":
            self._flush_text()
            attributes = dict(attrs)
            image_url = WeiboParser._normalize_url(
                attributes.get("data-src") or attributes.get("src")
            )
            if image_url:
                self.contents.append(OrderedContent(kind="image", value=image_url))

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "noscript"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in {"p", "div", "li", "blockquote", "h1", "h2", "h3"}:
            self._flush_text()

    def handle_data(self, data: str):
        if not self._ignored_depth and (text := data.strip()):
            self._text_parts.append(text)

    def close(self):
        super().close()
        self._flush_text()

    def _flush_text(self):
        text = " ".join(self._text_parts).strip()
        self._text_parts.clear()
        if text:
            self.contents.append(OrderedContent(kind="text", value=text))


class WeiboParser(BaseParser):
    """解析微博状态、视频页、长文章和分享链接。"""

    name = "weibo"
    display_name = "微博"
    cookie_config_key = "weibo_cookies"
    image_host_suffixes = ("sinaimg.cn", "sinaimg.com")
    STATUS_PATTERNS = (
        r"https?://(?:www\.)?weibo\.com/\d+/(?P<desktop_id>[0-9A-Za-z]+)",
        r"https?://m\.weibo\.cn/(?:status|detail|\d+)/(?P<mobile_id>[0-9A-Za-z]+)",
    )
    TV_PATTERN = (
        r"https?://(?:www\.)?weibo\.com/tv/show/\d{4}:\d+"
        r"\?[^\s#]*\bmid=(?P<mid>\d+)[^\s#]*"
    )
    VIDEO_PATTERN = (
        r"https?://video\.weibo\.com/show\?[^\s#]*"
        r"\bfid=(?P<fid>\d+:\d+)[^\s#]*"
    )
    SHARE_PATTERN = r"https?://mapp\.api\.weibo\.cn/fx/[A-Za-z0-9]+\.html"
    ARTICLE_PATTERNS = (
        r"https?://(?:www\.)?weibo\.com/ttarticle/[^\s#]*[?&#]id=(?P<article_query_id>\d+)",
        r"https?://card\.weibo\.com/article/[^\s#]*/id/(?P<article_path_id>\d+)",
    )
    PATTERNS = (
        *STATUS_PATTERNS,
        TV_PATTERN,
        VIDEO_PATTERN,
        SHARE_PATTERN,
        *ARTICLE_PATTERNS,
    )
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    }
    AUTH_PATH_MARKERS = ("/login", "/passport/", "/signin")

    async def match(self, context: ParseContext) -> bool:
        return any(
            re.search(pattern, context.combined_text) for pattern in self.PATTERNS
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        text = context.combined_text
        if match := re.search(self.TV_PATTERN, text):
            return await self._parse_status_id(self._mid_to_bid(match.group("mid")))
        if match := re.search(self.VIDEO_PATTERN, text):
            return await self._parse_video_fid(match.group("fid"))
        for pattern in self.ARTICLE_PATTERNS:
            if match := re.search(pattern, text):
                article_id = match.groupdict().get(
                    "article_query_id"
                ) or match.groupdict().get("article_path_id")
                return await self._parse_article(str(article_id))
        if match := re.search(self.SHARE_PATTERN, text):
            return await self._parse_share(match.group(0))
        for pattern in self.STATUS_PATTERNS:
            if match := re.search(pattern, text):
                status_id = match.groupdict().get(
                    "desktop_id"
                ) or match.groupdict().get("mobile_id")
                return await self._parse_status_id(str(status_id))
        return ParseResult(platform=self.name, error="未找到微博链接。")

    def _timeout(self) -> float:
        return self.request_timeout

    def _cookies(self) -> httpx.Cookies:
        return build_cookies(
            cookie_config_value(self.config, "weibo_cookies"),
            (".weibo.com", ".weibo.cn"),
        )

    @classmethod
    def _is_trusted_weibo_url(cls, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host in {"weibo.com", "weibo.cn"} or host.endswith(
            (".weibo.com", ".weibo.cn")
        )

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
        # 状态接口显式使用匿名会话，避免分享跳转携带登录 Cookie。
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

    async def _parse_share(self, url: str) -> ParseResult:
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=True,
            headers=self.HEADERS,
            cookies=self._cookies(),
        ) as client:
            response = await client.get(url)
            self.raise_for_response_status(response)
        final_url = str(response.url)
        if final_url == url:
            raise ValueError("微博分享链接未发生跳转")
        if self._is_auth_url(final_url):
            raise self.cookie_access_error()
        if not self._is_trusted_weibo_url(final_url) or not any(
            re.search(pattern, final_url) for pattern in self.PATTERNS
        ):
            raise ValueError("微博分享链接跳转到不可信域名")
        return await self.parse(ParseContext(text=final_url))

    async def _parse_article(self, article_id: str) -> ParseResult:
        referer = f"https://card.weibo.com/article/m/show/id/{article_id}"
        headers = {**self.HEADERS, "Referer": referer}
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=headers,
            cookies=self._cookies(),
        ) as client:
            response = await client.post(
                "https://card.weibo.com/article/m/aj/detail",
                data={"_rid": str(uuid4()), "id": article_id, "_t": int(time() * 1000)},
            )
            self.raise_for_response_status(response)
            payload = response.json()
            self._raise_for_payload_cookie_error(payload)
            result = self._parse_article_payload(payload)
            return await self.materialize_images(result, client, referer)

    @classmethod
    def _parse_article_payload(cls, payload: object) -> ParseResult:
        if not isinstance(payload, dict) or payload.get("msg") != "success":
            raise ValueError("微博长文章请求失败")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("微博长文章数据为空")
        user = data.get("userinfo")
        if not isinstance(user, dict) or not user.get("screen_name"):
            raise ValueError("微博长文章作者数据为空")
        parser = _WeiboArticleParser()
        parser.feed(str(data.get("content") or ""))
        parser.close()
        return ParseResult(
            platform=cls.name,
            title=str(data.get("title") or "微博长文章"),
            author=str(user["screen_name"]),
            ordered_contents=parser.contents,
            extra_lines=[] if parser.contents else ["微博长文章正文为空。"],
        )

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

    @staticmethod
    def _base62_encode(number: int) -> str:
        """将非负整数编码为微博使用的 base62 字符串。"""
        if number < 0:
            raise ValueError("微博 mid 不能为负数")
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if number == 0:
            return "0"
        encoded = ""
        while number:
            number, remainder = divmod(number, 62)
            encoded = alphabet[remainder] + encoded
        return encoded

    @classmethod
    def _mid_to_bid(cls, mid: str) -> str:
        """把十进制微博 mid 按七位分块转换为 base62 短 ID。"""
        if not mid.isdigit():
            raise ValueError("微博 mid 格式无效")
        reversed_mid = mid[::-1]
        chunks = []
        for offset in range(0, len(reversed_mid), 7):
            decimal_chunk = reversed_mid[offset : offset + 7][::-1]
            encoded = cls._base62_encode(int(decimal_chunk))
            if offset + 7 < len(reversed_mid):
                encoded = encoded.zfill(4)
            chunks.append(encoded)
        return "".join(reversed(chunks))

    @staticmethod
    def _normalize_url(value: object) -> str:
        if not isinstance(value, str) or not value:
            return ""
        if value.startswith("//"):
            return f"https:{value}"
        return value if value.startswith(("http://", "https://")) else ""

    @staticmethod
    def _strip_html(value: object) -> str:
        if not isinstance(value, str):
            return ""
        text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
        text = re.sub(r"</(?:p|div|li|blockquote)\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).replace("\u200b", "").replace("\xa0", " ")
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @classmethod
    def _is_auth_url(cls, url: str) -> bool:
        path = urlparse(url).path.lower()
        return any(marker in path for marker in cls.AUTH_PATH_MARKERS)

    def _raise_for_payload_cookie_error(self, payload: object) -> None:
        """识别微博业务载荷明确返回的登录和鉴权错误。"""
        if not isinstance(payload, dict):
            return
        message = str(
            payload.get("msg") or payload.get("message") or payload.get("errmsg") or ""
        ).lower()
        markers = ("login", "cookie", "未登录", "请登录", "权限")
        if any(marker in message for marker in markers):
            raise self.cookie_access_error()

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
