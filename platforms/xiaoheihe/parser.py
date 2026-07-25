import re

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.http import cookie_config_value, parse_cookie_header
from ...core.parser import BaseParser
from .game import (
    build_game_desc,
    build_game_result,
    canonical_game_web_url,
    extract_game_images,
    extract_game_videos,
    format_yuan_from_coin,
    parse_game_state,
    pick_steam_appid,
)
from .post import (
    clean_text,
    image_dedup_key,
    normalize_image_url,
    normalize_media_url,
    parse_post_contents,
    parse_post_payload,
)
from .signing import RequestSigner


class XiaoheiheParser(BaseParser):
    """负责小黑盒 URL 路由、会话准备与网络请求。"""

    name = "xiaoheihe"
    display_name = "小黑盒"
    cookie_config_key = "xiaoheihe_cookies"
    image_host_suffixes = ("max-c.com", "xiaoheihe.cn")
    AUTH_COOKIE_NAMES = ("pkey", "x_xhh_tokenid")
    CHAR_TABLE = RequestSigner.CHAR_TABLE
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

    async def _parse_post_by_id(self, link_id: str) -> ParseResult:
        params = {
            "app": "heybox",
            "os_type": "web",
            "x_app": "heybox_website",
            "x_client_type": "web",
            "x_os_type": "Windows",
            "x_client_version": "",
            "client_type": "web",
            "web_version": "3.0",
            "version": "999.0.4",
            "link_id": link_id,
            "is_first": "1",
            "page": "1",
            "index": "1",
            "limit": "20",
            "owner_only": "0",
            **self._sign_path("/bbs/app/link/tree"),
        }
        referer = f"https://www.xiaoheihe.cn/app/bbs/link/{link_id}"
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=self.HEADERS,
        ) as client:
            response = await client.get(
                "https://api.xiaoheihe.cn/bbs/app/link/tree",
                params=params,
                headers=self._request_cookie_headers(),
            )
            self.raise_for_response_status(response)
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                self._raise_for_payload_error(payload)
                raise ValueError("小黑盒 link/tree 请求失败")
            result_root = payload.get("result")
            if not isinstance(result_root, dict):
                raise ValueError("小黑盒 link/tree 结果为空")
            result = parse_post_payload(result_root)
            return await self.materialize_images(result, client, referer)

    async def _parse_game_by_appid(self, appid: str, game_type: str) -> ParseResult:
        appid = appid.strip()
        if not appid:
            raise ValueError("无效的小黑盒游戏 appid")
        cookie_headers = self._request_cookie_headers()
        web_url = canonical_game_web_url(appid, game_type)
        async with httpx.AsyncClient(
            timeout=self._timeout(),
            follow_redirects=False,
            headers=self.HEADERS,
        ) as client:
            detail_response = await client.get(
                "https://api.xiaoheihe.cn/game/get_game_detail/",
                params={
                    "app": "heybox",
                    "os_type": "web",
                    "x_app": "heybox_website",
                    "x_client_type": "web",
                    "x_os_type": "Windows",
                    "x_client_version": "",
                    "client_type": "web",
                    "web_version": "3.0",
                    "version": "999.0.4",
                    "steam_appid": appid,
                    **self._sign_path("/game/get_game_detail/"),
                },
                headers=cookie_headers,
            )
            self.raise_for_response_status(detail_response)
            detail_payload = detail_response.json()
            if (
                not isinstance(detail_payload, dict)
                or detail_payload.get("status") != "ok"
                or not isinstance(detail_payload.get("result"), dict)
            ):
                self._raise_for_payload_error(detail_payload)
                raise ValueError("小黑盒 get_game_detail 请求失败")
            game = detail_payload["result"]
            steam_appid = pick_steam_appid(game, appid)
            intro: dict = {}
            if steam_appid is not None:
                intro_response = await client.get(
                    "https://api.xiaoheihe.cn/game/game_introduction/",
                    params={"steam_appid": steam_appid, "return_json": 1},
                    headers=cookie_headers,
                )
                intro_response.raise_for_status()
                intro_payload = intro_response.json()
                if (
                    isinstance(intro_payload, dict)
                    and intro_payload.get("status") == "ok"
                    and isinstance(intro_payload.get("result"), dict)
                ):
                    intro = intro_payload["result"]
            result = build_game_result("", game, appid, game_type, intro)
            return await self.materialize_images(result, client, web_url)

    def _sign_path(self, path: str) -> dict[str, str | int]:
        return self._signer.sign_path(path)

    def _raise_for_payload_error(self, payload: object) -> None:
        """识别小黑盒业务载荷中的验证码、登录、令牌和权限错误。"""
        if not isinstance(payload, dict):
            return
        status = str(payload.get("status") or "").lower()
        if status in {"show_captcha", "captcha"}:
            raise ValueError(
                "小黑盒请求触发了人机验证，请稍后重试或配置有效 Cookies。"
            )
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

    def _ov(self, path: str, timestamp: int, nonce: str) -> str:
        return self._signer.ov(path, timestamp, nonce)

    @classmethod
    def _parse_post_payload(cls, payload: object) -> ParseResult:
        return parse_post_payload(payload)

    @classmethod
    def _parse_post_contents(cls, raw_text: object):
        return parse_post_contents(raw_text)

    @staticmethod
    def _clean_text(text: str) -> str:
        return clean_text(text)

    @staticmethod
    def _normalize_media_url(value: object) -> str:
        return normalize_media_url(value)

    @classmethod
    def _normalize_image_url(cls, value: object) -> str:
        return normalize_image_url(value)

    @staticmethod
    def _image_dedup_key(url: str) -> str:
        return image_dedup_key(url)

    @staticmethod
    def _canonical_game_web_url(appid: str, game_type: str) -> str:
        return canonical_game_web_url(appid, game_type)

    def _parse_game_state(
        self, html_text: str, appid: str, game_type: str, intro: dict
    ) -> ParseResult:
        return parse_game_state(html_text, appid, game_type, intro)

    def _build_game_desc(self, html_text: str, game: dict, intro: dict) -> str:
        return build_game_desc(html_text, game, intro)

    def _extract_game_images(self, game: dict, html_text: str) -> list[str]:
        return extract_game_images(game, html_text)

    def _extract_game_videos(self, game: dict, html_text: str) -> list[str]:
        return extract_game_videos(game, html_text)

    @staticmethod
    def _format_yuan_from_coin(coin) -> str:
        return format_yuan_from_coin(coin)
