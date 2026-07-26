"""识别小黑盒链接并分派到帖子或游戏内容解析器。"""

import re

from ...core.contracts import ParseContext, ParseResult
from ...core.http import cookie_config_value, parse_cookie_header
from ...core.parser import BaseParser
from .game import XiaoheiheGameContent
from .post import XiaoheihePostContent
from .signing import RequestSigner


class XiaoheiheParser(XiaoheihePostContent, XiaoheiheGameContent, BaseParser):
    """负责小黑盒 URL 路由、凭据白名单与请求签名。"""

    name = "xiaoheihe"
    display_name = "小黑盒"
    cookie_config_key = "xiaoheihe_cookies"
    image_host_suffixes = ("max-c.com", "xiaoheihe.cn")
    AUTH_COOKIE_NAMES = ("pkey", "x_xhh_tokenid")
    BBS_WEB_PATTERN = (
        r"https?://(?:www\.)?xiaoheihe\.cn/app/bbs/link/"
        r"(?P<link_id>[0-9a-z]+)"
    )
    BBS_SHARE_PATTERN = (
        r"https?://api\.xiaoheihe\.cn/v3/bbs/app/api/(?:web/)?share"
        r"\?[^\s#]*\blink_id=(?P<share_link_id>[0-9a-z]+)[^\s#]*"
    )
    GAME_WEB_PATTERN = (
        r"https?://(?:www\.)?xiaoheihe\.cn/app/topic/game/"
        r"(?P<game_type>[a-z]+)/(?P<appid>[0-9a-z]+)"
    )
    GAME_SHARE_PATTERN = (
        r"https?://api\.xiaoheihe\.cn/game/share_game_detail\?[^\s#]*"
        r"\bappid=(?P<share_appid>[0-9a-z]+)[^\s#]*"
        r"\bgame_type=(?P<share_game_type>[a-z]+)[^\s#]*"
    )
    PATTERNS = (
        BBS_WEB_PATTERN,
        BBS_SHARE_PATTERN,
        GAME_WEB_PATTERN,
        GAME_SHARE_PATTERN,
    )
    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.xiaoheihe.cn/",
        "Origin": "https://www.xiaoheihe.cn",
    }

    def __init__(self, config) -> None:
        super().__init__(config)
        self._signer = RequestSigner()

    async def match(self, context: ParseContext) -> bool:
        return any(
            re.search(pattern, context.combined_text) for pattern in self.PATTERNS
        )

    async def parse(self, context: ParseContext) -> ParseResult:
        text = context.combined_text
        for pattern in (self.BBS_WEB_PATTERN, self.BBS_SHARE_PATTERN):
            if match := re.search(pattern, text):
                link_id = match.groupdict().get("link_id") or match.groupdict().get(
                    "share_link_id"
                )
                return await self._parse_post_by_id(str(link_id))
        if match := re.search(self.GAME_WEB_PATTERN, text):
            return await self._parse_game_by_appid(
                match.group("appid"), match.group("game_type")
            )
        if match := re.search(self.GAME_SHARE_PATTERN, text):
            return await self._parse_game_by_appid(
                match.group("share_appid"), match.group("share_game_type")
            )
        return ParseResult(platform=self.name, error="未找到小黑盒链接。")

    def _timeout(self) -> float:
        return self.request_timeout

    def _configured_cookie_values(self) -> dict[str, str]:
        """提取小黑盒请求实际使用的白名单配置字段。"""
        allowed = set(self.AUTH_COOKIE_NAMES)
        return {
            name: value
            for name, value in parse_cookie_header(
                cookie_config_value(self.config, "xiaoheihe_cookies")
            )
            if name in allowed and value
        }

    def _request_cookie_headers(self) -> dict[str, str]:
        """仅在已配置登录凭据时构造小黑盒请求 Cookie。"""
        configured = self._configured_cookie_values()
        cookie_header = "; ".join(
            f"{name}={configured[name]}"
            for name in self.AUTH_COOKIE_NAMES
            if configured.get(name)
        )
        return {"Cookie": cookie_header} if cookie_header else {}

    def _sign_path(self, path: str) -> dict[str, str | int]:
        return self._signer.sign_path(path)

    def _raise_for_payload_error(self, payload: object) -> None:
        """识别小黑盒业务载荷中的验证码、登录、令牌和权限错误。"""
        if not isinstance(payload, dict):
            return
        status = str(payload.get("status") or "").lower()
        if status in {"show_captcha", "captcha"}:
            raise ValueError("小黑盒请求触发了人机验证，请稍后重试或配置有效 Cookies。")
        message = str(payload.get("msg") or payload.get("message") or "").lower()
        markers = (
            "denied",
            "unauthorized",
            "forbidden",
            "login",
            "token",
            "登录",
            "权限",
        )
        if any(marker in message for marker in markers):
            raise self.cookie_access_error()
