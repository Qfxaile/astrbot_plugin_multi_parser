"""实现知乎二维码登录与 Cookie 提取。"""

import json
import re
from urllib.parse import urljoin, urlsplit

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
    render_login_qr_png,
)


class ZhihuLoginProvider(HTTPPlatformLoginProvider):
    """通过知乎官方网页二维码接口建立管理员登录态。"""

    display_name = "知乎"
    cookie_config_key = "zhihu_cookies"
    BOOTSTRAP_URL = "https://www.zhihu.com/signin"
    QR_GENERATE_URL = "https://www.zhihu.com/api/v3/account/api/login/qrcode"
    QR_POLL_URL_PREFIX = "https://www.zhihu.com/api/v3/account/api/login/qrcode"
    CURRENT_USER_URL = "https://www.zhihu.com/api/v4/me?include=is_realname"
    QR_EXPIRES_IN_SECONDS = 180
    MAX_RESPONSE_BYTES = 64 * 1024
    MAX_BOOTSTRAP_RESPONSE_BYTES = 2 * 1024 * 1024
    MAX_BOOTSTRAP_PREFIX_BYTES = 64 * 1024
    MAX_BOOTSTRAP_REDIRECTS = 3
    BROWSER_ID_NAME = "x-du-bid"
    COOKIE_NAMES = ("z_c0", "d_c0")
    LOGIN_HOST_SUFFIXES = ("zhihu.com",)
    RISK_CONTROL_CODES = frozenset({40321, 410001})
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    _SESSION_KEY_PATTERN = re.compile(r"[A-Za-z0-9._~=-]{1,512}")
    _BROWSER_ID_PATTERN = re.compile(r"[A-Za-z0-9._~+/=-]{1,512}")

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
                "Referer": "https://www.zhihu.com/signin",
                "Origin": "https://www.zhihu.com",
            },
        )

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建二维码会话，并在内存中渲染受信任的登录链接。"""
        browser_id = await self._bootstrap_browser_id()
        payload = await self._request_payload(
            "POST",
            self.QR_GENERATE_URL,
            headers={self.BROWSER_ID_NAME: browser_id},
        )
        data = self._payload_data(payload)
        login_url = str(data.get("link") or "")
        session_key = str(data.get("token") or "")
        if (
            len(login_url) > 2048
            or not self._is_trusted_https_url(login_url)
            or self._SESSION_KEY_PATTERN.fullmatch(session_key) is None
        ):
            raise PlatformLoginError("知乎返回了无效的二维码登录信息。")

        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=render_login_qr_png(login_url),
            expires_in_seconds=self.QR_EXPIRES_IN_SECONDS,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询扫码状态，成功时提取知乎域的最小 Cookie 集合。"""
        if self._SESSION_KEY_PATTERN.fullmatch(session_key) is None:
            raise PlatformLoginError("知乎登录会话无效，请重新发起登录。")

        payload = await self._request_payload(
            "GET",
            f"{self.QR_POLL_URL_PREFIX}/{session_key}/scan_info",
        )
        data = self._payload_data(payload)
        if data.get("accessToken") or data.get("access_token"):
            cookie_header = self._cookie_header()
            if not cookie_header.startswith("z_c0="):
                raise PlatformLoginError("知乎登录成功，但响应中缺少有效登录凭据。")
            return LoginPollResult(LoginPollState.SUCCESS, cookie_header)

        status = str(data.get("status") if "status" in data else "").lower()
        if status == "0":
            return LoginPollResult(LoginPollState.WAITING)
        if status == "1":
            return LoginPollResult(LoginPollState.SCANNED)
        if status == "5":
            # 官方网页会把新令牌渲染为新二维码；私聊无法替换已发送图片。
            return LoginPollResult(LoginPollState.EXPIRED)
        raise PlatformLoginError("知乎返回了无法识别的登录状态，请重新发起登录。")

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        payload = await self._request_payload(
            "GET",
            self.CURRENT_USER_URL,
            headers={"Cookie": cookie_header},
        )
        user_id = str(payload.get("id") or payload.get("url_token") or "").strip()
        display_name = str(payload.get("name") or "").strip()
        if not user_id and not display_name:
            return None
        return PlatformUser(user_id=user_id, display_name=display_name)

    async def _bootstrap_browser_id(self) -> str:
        """从知乎官方响应取得二维码接口要求的浏览器设备标识。"""
        current_url = self.BOOTSTRAP_URL
        browser_id = ""
        try:
            for redirect_count in range(self.MAX_BOOTSTRAP_REDIRECTS + 1):
                if not self._is_trusted_https_url(current_url):
                    raise PlatformLoginError("知乎登录初始化返回了不安全的重定向。")
                async with self._client.stream(
                    "GET",
                    current_url,
                    follow_redirects=False,
                ) as response:
                    response_browser_id = self._browser_id_from_response(response)
                    if response_browser_id:
                        browser_id = response_browser_id
                    if response.is_redirect:
                        location = response.headers.get("Location", "")
                        redirect_url = urljoin(current_url, location)
                        if (
                            redirect_count >= self.MAX_BOOTSTRAP_REDIRECTS
                            or self._is_verification_url(redirect_url)
                            or not self._is_trusted_https_url(redirect_url)
                        ):
                            if self._is_verification_url(redirect_url):
                                raise self._verification_error()
                            raise PlatformLoginError(
                                "知乎登录初始化返回了不安全的重定向。"
                            )
                        current_url = redirect_url
                        continue
                    if response.status_code in {401, 403, 429}:
                        raise self._verification_error()
                    response.raise_for_status()
                    prefix = bytearray()
                    total_bytes = 0
                    content_length = response.headers.get("Content-Length")
                    if content_length:
                        try:
                            if int(content_length) > self.MAX_BOOTSTRAP_RESPONSE_BYTES:
                                raise PlatformLoginError(
                                    "知乎登录初始化响应超过安全限制。"
                                )
                        except ValueError:
                            pass
                    async for chunk in response.aiter_bytes():
                        total_bytes += len(chunk)
                        if total_bytes > self.MAX_BOOTSTRAP_RESPONSE_BYTES:
                            raise PlatformLoginError("知乎登录初始化响应超过安全限制。")
                        remaining = self.MAX_BOOTSTRAP_PREFIX_BYTES - len(prefix)
                        if remaining > 0:
                            prefix.extend(chunk[:remaining])
                if self._looks_like_verification_page(bytes(prefix)):
                    raise self._verification_error()
                if browser_id:
                    return browser_id
                break
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError("知乎登录初始化请求失败，请稍后重试。") from exc

        raise PlatformLoginError(
            "知乎二维码登录需要官方浏览器下发的 x-du-bid，"
            "当前私聊流程无法自动初始化，请手工配置 Cookies。"
        )

    async def _request_payload(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict:
        try:
            async with self._client.stream(
                method,
                url,
                headers=headers,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    raise PlatformLoginError("知乎登录服务返回了不安全的重定向。")
                content = await read_login_response_body(
                    response,
                    limit=self.MAX_RESPONSE_BYTES,
                    platform=self.display_name,
                )
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status_code in {401, 403, 429}:
                    raise self._verification_error()
                if (
                    response.status_code == 400
                    and method == "POST"
                    and url == self.QR_GENERATE_URL
                    and self._requires_browser_id(content)
                ):
                    raise PlatformLoginError(
                        "知乎二维码登录需要有效的官方浏览器设备标识，"
                        "当前私聊流程无法安全初始化，请手工配置 Cookies。"
                    )
                response.raise_for_status()

            stripped = content.lstrip()
            if "text/html" in content_type or stripped.startswith(b"<"):
                raise self._verification_error()
            payload = json.loads(content)
        except PlatformLoginError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformLoginError("知乎登录服务请求失败，请稍后重试。") from exc

        if not isinstance(payload, dict):
            raise PlatformLoginError("知乎登录服务返回了无效响应。")
        if self._requires_verification(payload):
            raise self._verification_error()
        return payload

    @staticmethod
    def _payload_data(payload: dict) -> dict:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    def _cookie_header(self) -> str:
        return cookie_header_from_jar(
            self._client.cookies.jar,
            self.COOKIE_NAMES,
            domain_allowed=self._is_trusted_cookie_domain,
        )

    @classmethod
    def _is_trusted_cookie_domain(cls, domain: str) -> bool:
        return host_matches(domain, cls.LOGIN_HOST_SUFFIXES)

    @classmethod
    def _browser_id_from_response(cls, response: httpx.Response) -> str:
        """只接受知乎可信响应明确下发的同名响应头或 Cookie。"""
        header_value = response.headers.get(cls.BROWSER_ID_NAME, "")
        if cls._valid_browser_id(header_value):
            return header_value
        for cookie in response.cookies.jar:
            domain = str(cookie.domain or "").lstrip(".").lower()
            if (
                cookie.name == cls.BROWSER_ID_NAME
                and cls._is_trusted_cookie_domain(domain)
                and cls._valid_browser_id(cookie.value)
            ):
                return str(cookie.value)
        return ""

    @classmethod
    def _valid_browser_id(cls, value: object) -> bool:
        return cls._BROWSER_ID_PATTERN.fullmatch(str(value or "")) is not None

    @classmethod
    def _requires_verification(cls, payload: dict) -> bool:
        containers = [payload]
        for key in ("data", "error"):
            value = payload.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            code = container.get("code")
            try:
                if int(code) in cls.RISK_CONTROL_CODES:
                    return True
            except (TypeError, ValueError):
                pass
            if any(
                container.get(name)
                for name in (
                    "captcha",
                    "challenge",
                    "needCaptcha",
                    "needDeviceVerify",
                    "needVerification",
                    "unhuman",
                    "verifyTicket",
                    "verify_ticket",
                )
            ):
                return True
            for name in ("detail", "error", "message"):
                message = container.get(name)
                if not isinstance(message, str):
                    continue
                normalized = message.lower()
                if any(
                    marker in normalized
                    for marker in (
                        "captcha",
                        "challenge",
                        "device verify",
                        "risk control",
                        "unhuman",
                        "人机验证",
                        "安全验证",
                        "设备验证",
                    )
                ):
                    return True
        return False

    @classmethod
    def _requires_browser_id(cls, content: bytes) -> bool:
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        containers = [payload]
        for name in ("data", "error"):
            value = payload.get(name)
            if isinstance(value, dict):
                containers.append(value)
        if not any(container.get("code") in {1000, "1000"} for container in containers):
            return False
        for container in containers:
            for name, value in container.items():
                if cls._has_browser_id_marker(name):
                    return True
                if isinstance(value, str) and cls._has_browser_id_marker(value):
                    return True
        return False

    @staticmethod
    def _has_browser_id_marker(value: str) -> bool:
        normalized = value.lower().replace("_", "-")
        return any(
            marker in normalized
            for marker in (
                "x-du-bid",
                "device id",
                "device-id",
                "device identifier",
                "device-identifier",
                "browser id",
                "browser-id",
                "设备标识",
            )
        )

    @staticmethod
    def _looks_like_verification_page(content: bytes) -> bool:
        lowered = content.lower()
        return any(
            marker in lowered
            for marker in (
                b"/account/unhuman",
                b'id="captcha"',
                b"captcha-container",
                b"geetest_holder",
            )
        )

    @classmethod
    def _is_verification_url(cls, url: str) -> bool:
        if not url:
            return False
        try:
            path = urlsplit(url).path.lower()
        except ValueError:
            return False
        return any(
            marker in path for marker in ("/account/unhuman", "/captcha", "/verify")
        )

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "知乎登录触发了平台人机或设备验证，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )

    @classmethod
    def _is_trusted_https_url(cls, url: str) -> bool:
        return is_trusted_https_url(url, cls.LOGIN_HOST_SUFFIXES)
