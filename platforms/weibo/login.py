"""实现微博二维码登录与最小 Cookie 提取。"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from ...core.http import cookie_header_from_jar, host_matches, is_trusted_https_url
from ...core.platform_login import (
    HTTPPlatformLoginProvider,
    LoginPollResult,
    LoginPollState,
    PlatformLoginError,
    PlatformUser,
    QRLoginChallenge,
    read_login_response_body,
)


class WeiboLoginProvider(HTTPPlatformLoginProvider):
    """通过微博官方 Web 二维码接口建立管理员登录态。"""

    display_name = "微博"
    cookie_config_key = "weibo_cookies"
    QR_GENERATE_URL = "https://login.sina.com.cn/sso/qrcode/image"
    QR_POLL_URL = "https://login.sina.com.cn/sso/qrcode/check"
    SSO_LOGIN_URL = "https://login.sina.com.cn/sso/login.php"
    LOGIN_PAGE_URL = "https://weibo.com/"
    CURRENT_USER_URL = "https://weibo.com/ajax/config/get_config"
    QR_EXPIRES_IN_SECONDS = 180
    MAX_RESPONSE_BYTES = 64 * 1024
    MAX_QR_IMAGE_BYTES = 512 * 1024
    MAX_CROSS_DOMAIN_URLS = 5
    MAX_LOGIN_REDIRECTS = 5
    MAX_CONFIRMATION_DEPTH = 6
    MAX_CONFIRMATION_CONTAINERS = 128
    COOKIE_NAMES = ("SUB",)
    QR_IMAGE_HOST_SUFFIXES = ("qr.weibo.cn",)
    SUCCESS_HOST_SUFFIXES = ("weibo.com", "weibo.cn")
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    _QRID_PATTERN = re.compile(r"[A-Za-z0-9._~-]{1,256}")
    _CALLBACK_PATTERN = re.compile(r"STK_[0-9]{10,32}")
    _REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        config,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            config,
            client=client,
            follow_redirects=False,
            headers={
                "User-Agent": self.USER_AGENT,
                "Referer": self.LOGIN_PAGE_URL,
            },
        )

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建二维码会话并下载受信任的微博二维码图片。"""
        callback = self._callback_name()
        payload = await self._get_jsonp_payload(
            self.QR_GENERATE_URL,
            callback=callback,
            params={
                "entry": "weibo",
                "size": "180",
                "callback": callback,
            },
        )
        data = payload.get("data")
        if str(payload.get("retcode") or "") != "20000000" or not isinstance(
            data, dict
        ):
            raise PlatformLoginError("微博暂时无法创建登录二维码，请稍后重试。")

        session_key = str(data.get("qrid") or "")
        image_url = self._normalize_qr_image_url(data.get("image"))
        if (
            self._QRID_PATTERN.fullmatch(session_key) is None
            or len(image_url) > 2048
            or not self._is_trusted_https_url(image_url, self.QR_IMAGE_HOST_SUFFIXES)
        ):
            raise PlatformLoginError("微博返回了无效的二维码登录信息。")

        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=await self._download_qr_image(image_url),
            expires_in_seconds=self.QR_EXPIRES_IN_SECONDS,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询扫码状态，成功后完成受控 SSO 并提取必要 Cookie。"""
        if self._QRID_PATTERN.fullmatch(session_key) is None:
            raise PlatformLoginError("微博登录会话无效，请重新发起登录。")

        callback = self._callback_name()
        payload = await self._get_jsonp_payload(
            self.QR_POLL_URL,
            callback=callback,
            params={
                "entry": "weibo",
                "qrid": session_key,
                "callback": callback,
            },
        )
        retcode = str(payload.get("retcode") or "")
        if retcode == "50114001":
            return LoginPollResult(LoginPollState.WAITING)
        if retcode == "50114002":
            return LoginPollResult(LoginPollState.SCANNED)
        if retcode in {"50114003", "50114004", "50114015"}:
            return LoginPollResult(LoginPollState.EXPIRED)
        if retcode != "20000000":
            raise PlatformLoginError("微博返回了无法识别的登录状态，请重新发起登录。")

        alt = self._find_sso_token(payload)
        if not alt:
            raise PlatformLoginError("微博返回了无效的登录确认信息。")

        await self._complete_sso_login(alt)
        cookie_header = self._cookie_header()
        if "SUB=" not in cookie_header:
            raise PlatformLoginError("微博登录成功，但响应中缺少有效登录凭据。")
        return LoginPollResult(LoginPollState.SUCCESS, cookie_header)

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        content, _ = await self._read_limited_response(
            self.CURRENT_USER_URL,
            limit=self.MAX_RESPONSE_BYTES,
            headers={
                "Cookie": cookie_header,
                "Client-Version": "3.0.0",
                "Referer": self.LOGIN_PAGE_URL,
            },
        )
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("ok") not in {1, "1"}:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        user = data.get("user")
        if not isinstance(user, dict):
            user = data.get("userInfo")
        if not isinstance(user, dict):
            user = {}
        user_id = str(
            user.get("idstr")
            or user.get("id")
            or user.get("uid")
            or data.get("uid")
            or ""
        ).strip()
        display_name = str(
            user.get("screen_name") or user.get("name") or data.get("screen_name") or ""
        ).strip()
        if not user_id and not display_name:
            return None
        return PlatformUser(user_id=user_id, display_name=display_name)

    async def _complete_sso_login(self, alt: str) -> None:
        callback = self._callback_name()
        payload = await self._get_jsonp_payload(
            self.SSO_LOGIN_URL,
            callback=callback,
            params={
                "entry": "qrcodesso",
                "returntype": "TEXT",
                "crossdomain": "1",
                "cdult": "3",
                "domain": "weibo.com",
                "alt": alt,
                "savestate": "30",
                "callback": callback,
            },
        )
        urls = payload.get("crossDomainUrlList")
        if str(payload.get("retcode")) != "0" or not isinstance(urls, list):
            raise PlatformLoginError("微博登录确认失败，请重新发起登录。")
        if not urls or len(urls) > self.MAX_CROSS_DOMAIN_URLS:
            raise PlatformLoginError("微博返回了无效的登录确认信息。")

        # SSO 返回一次性跨域地址。逐个验证外层 URL，并手动限制重定向，
        # 避免令牌被自动带往列表之外的主机。
        trusted_urls: list[str] = []
        for value in urls:
            if not isinstance(value, str):
                continue
            url = value.strip()
            # SSO 可能同时返回其他产品的跨域地址。只保留微博受信任域，
            # 未知地址不得参与请求，也不能让它阻断可用的微博登录地址。
            if len(url) > 2048 or not self._is_trusted_success_url(url):
                continue
            trusted_urls.append(self._with_cross_domain_action(url))
        if not trusted_urls:
            raise PlatformLoginError("微博返回了无效的登录确认信息。")
        for url in trusted_urls:
            await self._visit_success_url(url)

    async def _visit_success_url(self, url: str) -> None:
        current_url = url
        for _ in range(self.MAX_LOGIN_REDIRECTS + 1):
            if not self._is_trusted_success_url(current_url):
                raise PlatformLoginError("微博返回了无效的登录确认信息。")
            try:
                async with self._client.stream(
                    "GET", current_url, follow_redirects=False
                ) as response:
                    if response.status_code in self._REDIRECT_STATUS_CODES:
                        location = response.headers.get("Location", "")
                    else:
                        response.raise_for_status()
                        content = await read_login_response_body(
                            response,
                            limit=self.MAX_RESPONSE_BYTES,
                            platform=self.display_name,
                        )
                        if self._contains_verification_markers(content):
                            raise self._verification_error()
                        return
            except PlatformLoginError:
                raise
            except httpx.HTTPError as exc:
                raise PlatformLoginError("微博登录确认请求失败，请稍后重试。") from exc

            if not location or len(location) > 2048:
                raise PlatformLoginError("微博返回了无效的登录确认信息。")
            current_url = urljoin(current_url, location)
        raise PlatformLoginError("微博登录确认重定向次数超过安全限制。")

    async def _get_jsonp_payload(
        self,
        url: str,
        *,
        callback: str,
        params: dict[str, str],
    ) -> dict:
        try:
            content, _ = await self._read_limited_response(
                url,
                limit=self.MAX_RESPONSE_BYTES,
                params=params,
            )
            if self._contains_verification_markers(content):
                raise self._verification_error()
            payload = self._parse_jsonp(content, callback)
        except PlatformLoginError:
            raise
        except ValueError as exc:
            raise PlatformLoginError("微博登录服务返回了无效响应。") from exc
        if not isinstance(payload, dict):
            raise PlatformLoginError("微博登录服务返回了无效响应。")
        if self._requires_verification(payload):
            raise self._verification_error()
        return payload

    async def _download_qr_image(self, url: str) -> bytes:
        content, content_type = await self._read_limited_response(
            url,
            limit=self.MAX_QR_IMAGE_BYTES,
        )
        media_type = content_type.split(";", 1)[0].strip()
        supported_type = media_type in {"image/jpeg", "image/png"}
        supported_signature = content.startswith(
            (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")
        )
        if not supported_type or not supported_signature:
            raise PlatformLoginError("微博返回了无效的登录二维码图片。")
        return content

    async def _read_limited_response(
        self,
        url: str,
        *,
        limit: int,
        **kwargs,
    ) -> tuple[bytes, str]:
        try:
            async with self._client.stream(
                "GET", url, follow_redirects=False, **kwargs
            ) as response:
                if response.status_code in self._REDIRECT_STATUS_CODES:
                    raise PlatformLoginError("微博登录服务返回了不安全的重定向。")
                response.raise_for_status()
                content = await read_login_response_body(
                    response,
                    limit=limit,
                    platform=self.display_name,
                )
                return content, response.headers.get("Content-Type", "").lower()
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError("微博登录服务请求失败，请稍后重试。") from exc

    def _cookie_header(self) -> str:
        return cookie_header_from_jar(
            self._client.cookies.jar,
            self.COOKIE_NAMES,
            domain_allowed=self._is_trusted_cookie_domain,
        )

    @classmethod
    def _is_trusted_cookie_domain(cls, domain: str) -> bool:
        return host_matches(domain, cls.SUCCESS_HOST_SUFFIXES)

    @classmethod
    def _is_trusted_success_url(cls, url: str) -> bool:
        if is_trusted_https_url(
            url,
            cls.SUCCESS_HOST_SUFFIXES,
            allow_fragment=False,
        ):
            return True
        return is_trusted_https_url(
            url,
            ("login.sina.com.cn", "passport.sina.com.cn"),
            allow_fragment=False,
        )

    @staticmethod
    def _is_trusted_https_url(url: str, host_suffixes: tuple[str, ...]) -> bool:
        return is_trusted_https_url(url, host_suffixes, allow_fragment=False)

    @classmethod
    def _parse_jsonp(cls, content: bytes, callback: str) -> object:
        if cls._CALLBACK_PATTERN.fullmatch(callback) is None:
            raise ValueError("invalid callback")
        text = content.decode("utf-8-sig").strip()
        match = re.fullmatch(
            rf"(?:window\.{re.escape(callback)}\s*&&\s*)?"
            rf"{re.escape(callback)}\((.*)\)\s*;?",
            text,
            flags=re.DOTALL,
        )
        if match is None:
            raise ValueError("invalid jsonp")
        return json.loads(match.group(1))

    @staticmethod
    def _normalize_qr_image_url(value: object) -> str:
        if not isinstance(value, str):
            return ""
        if value.startswith("//"):
            return f"https:{value}"
        return value

    @staticmethod
    def _with_cross_domain_action(url: str) -> str:
        parsed = urlsplit(url)
        query_names = {name for name, _ in parse_qsl(parsed.query)}
        needs_action = parsed.path.rstrip("/").endswith("/crossdomain")
        if needs_action and "action" not in query_names:
            separator = "&" if parsed.query else "?"
            return f"{url}{separator}action=login"
        return url

    @staticmethod
    def _callback_name() -> str:
        return f"STK_{time.time_ns() // 100_000}"

    @staticmethod
    def _is_safe_sso_token(value: str) -> bool:
        """允许官方令牌扩展字符，同时拒绝控制字符和异常长度。"""
        return (
            bool(value)
            and len(value) <= 2048
            and all(character.isprintable() for character in value)
        )

    @classmethod
    def _find_sso_token(cls, payload: Mapping[str, object]) -> str:
        """在受限层级和容器数内查找官方精确 ``alt`` 字段。"""
        pending: list[tuple[object, int]] = [(payload, 0)]
        visited_containers = 0
        while pending and visited_containers < cls.MAX_CONFIRMATION_CONTAINERS:
            value, depth = pending.pop()
            if not isinstance(value, (Mapping, list)):
                continue
            visited_containers += 1
            if isinstance(value, Mapping):
                candidate = value.get("alt")
                if isinstance(candidate, str) and cls._is_safe_sso_token(candidate):
                    return candidate
                children = value.values()
            else:
                children = value
            if depth < cls.MAX_CONFIRMATION_DEPTH:
                pending.extend((child, depth + 1) for child in children)
        return ""

    @staticmethod
    def _contains_verification_markers(content: bytes) -> bool:
        text = content.decode("utf-8", errors="ignore").lower()
        return any(
            marker in text
            for marker in (
                "geetest",
                "captcha",
                "verify_ticket",
                "security verification",
                "安全验证",
                "人机验证",
                "设备验证",
            )
        )

    @staticmethod
    def _requires_verification(payload: dict) -> bool:
        for container in (payload, payload.get("data")):
            if not isinstance(container, dict):
                continue
            if any(
                container.get(name)
                for name in (
                    "captcha",
                    "geetest",
                    "verify_ticket",
                    "risk_control",
                )
            ):
                return True
            message = str(
                container.get("msg")
                or container.get("message")
                or container.get("errmsg")
                or ""
            ).lower()
            if any(
                marker in message for marker in ("安全验证", "人机验证", "设备验证")
            ):
                return True
        return False

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "微博登录触发了平台人机或设备验证，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )
