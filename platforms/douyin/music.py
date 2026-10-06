import json
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse, urlsplit

from ...core.contracts import ParseResult

AUDIO_HOST_SUFFIXES = ("douyinvod.com",)


class _QishuiTrackHTMLParser(HTMLParser):
    """提取汽水音乐服务端渲染页面中的单曲字段。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.author = ""
        self.description = ""
        self.cover_url = ""
        self.audio_url = ""
        self.router_data_text = ""
        self._capture_field = ""
        self._capture_tag = ""
        self._text_parts: list[str] = []
        self._in_script = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "script":
            self._in_script = True
            self._script_parts = []
        elif tag == "meta" and attributes.get("name") == "description":
            self.description = (attributes.get("content") or "").strip()
        elif tag == "img" and attributes.get("alt") == "a-image":
            self.cover_url = (attributes.get("src") or "").strip()
        elif tag == "audio" and attributes.get("id") == "--luna-view-player--":
            self.audio_url = (attributes.get("src") or "").strip()
        elif tag == "h1" and "title" in classes:
            self._begin_capture("title", tag)
        elif tag == "span" and "artist-name-max" in classes:
            self._begin_capture("author", tag)

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_parts.append(data)
        if self._capture_field:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_script:
            script_text = "".join(self._script_parts).strip()
            if script_text.startswith("_ROUTER_DATA"):
                self.router_data_text = script_text
            self._in_script = False
            self._script_parts = []
        if tag != self._capture_tag:
            return
        value = "".join(self._text_parts).strip()
        if value:
            setattr(self, self._capture_field, value)
        self._capture_field = ""
        self._capture_tag = ""
        self._text_parts = []

    def _begin_capture(self, field_name: str, tag: str) -> None:
        self._capture_field = field_name
        self._capture_tag = tag
        self._text_parts = []


def is_qishui_track_url(url: str) -> bool:
    """判断重定向目标是否为带单曲 ID 的汽水音乐分享页。"""
    parsed = urlparse(url)
    track_id = (parse_qs(parsed.query).get("track_id") or [""])[0]
    return (
        parsed.hostname == "music.douyin.com"
        and parsed.path.rstrip("/") == "/qishui/share/track"
        and track_id.isdigit()
    )


def parse_qishui_track_html(html: str, *, platform: str) -> ParseResult:
    """将汽水音乐单曲页转换为统一解析结果。

    页面提供歌曲摘要、封面和播放器地址。音频地址只接受汽水音乐当前使用的
    受信任 CDN，避免把外部页面值直接交给协议端下载。

    参数:
        html: 汽水音乐单曲页 HTML。
        platform: 写入统一解析结果的平台名称。

    返回:
        包含歌曲摘要、封面候选和安全音频地址的解析结果。

    异常:
        ValueError: 页面中不存在歌曲标题时抛出。
    """
    parser = _QishuiTrackHTMLParser()
    parser.feed(html)
    if not parser.title:
        raise ValueError("汽水音乐分享页中未找到歌曲信息")

    audio_candidate = parser.audio_url or _audio_url_from_router_data(
        parser.router_data_text
    )
    audio_url = (
        audio_candidate
        if _is_safe_media_url(audio_candidate, AUDIO_HOST_SUFFIXES)
        else ""
    )
    extra_lines = [] if audio_url else ["无法获取安全的音频直链。"]
    result = ParseResult(platform=platform)
    result.content.title = parser.title
    result.content.author = parser.author or "未知歌手"
    result.content.description = parser.description
    if parser.cover_url:
        result.content.cover_urls.append(parser.cover_url)
    result.content.extra_lines.extend(extra_lines)
    result.media.audio_url = audio_url
    return result


def _audio_url_from_router_data(script_text: str) -> str:
    """从服务端注入的路由数据中提取单曲音频地址。"""
    prefix, separator, payload = script_text.partition("=")
    if not separator or prefix.strip() != "_ROUTER_DATA":
        return ""
    try:
        data, _ = json.JSONDecoder().raw_decode(payload.lstrip())
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    loader_data = data.get("loaderData")
    if not isinstance(loader_data, dict):
        return ""
    track_page = loader_data.get("track_page")
    if not isinstance(track_page, dict):
        return ""
    audio_option = track_page.get("audioWithLyricsOption")
    if not isinstance(audio_option, dict):
        return ""
    return str(audio_option.get("url") or "").strip()


def _is_safe_media_url(url: str, host_suffixes: tuple[str, ...]) -> bool:
    """校验交给协议端拉取的远程媒体地址。"""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        return (
            parsed.scheme in {"http", "https"}
            and bool(hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in {None, 80, 443}
            and any(
                hostname == suffix or hostname.endswith(f".{suffix}")
                for suffix in host_suffixes
            )
        )
    except ValueError:
        return False
