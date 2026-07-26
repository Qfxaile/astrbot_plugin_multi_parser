"""实现抖音二维码登录与 Cookie 提取。"""

import json
import re
from urllib.parse import urljoin

import httpx

from ...core.http import (
    cookie_config_value,
    cookie_header_from_jar,
    host_matches,
    is_trusted_https_url,
    parse_cookie_header,
)
from ...core.platform_login import (
    HTTPPlatformLoginProvider,
    LoginPollResult,
    LoginPollState,
    PlatformLoginError,
    PlatformUser,
    QRLoginChallenge,
    read_login_response_body,
)


class DouyinLoginProvider(HTTPPlatformLoginProvider):
    """通过抖音官方网页二维码接口建立管理员登录态。"""

    display_name = "抖音"
    cookie_config_key = "douyin_cookies"
    QR_GENERATE_URL = "https://sso.douyin.com/get_qrcode/"
    QR_POLL_URL = "https://sso.douyin.com/check_qrconnect/"
    TTWID_REGISTER_URL = "https://ttwid.bytedance.com/ttwid/union/register/"
    LOGIN_SERVICE_URL = "https://www.douyin.com/"
    CURRENT_USER_URL = "https://www.douyin.com/aweme/v1/web/user/profile/self/"
    QR_EXPIRES_IN_SECONDS = 180
    MAX_RESPONSE_BYTES = 64 * 1024
    MAX_QR_IMAGE_BYTES = 512 * 1024
    MAX_LOGIN_REDIRECTS = 5
    COOKIE_NAMES = (
        "sessionid",
        "sessionid_ss",
        "sid_guard",
        "sid_tt",
        "uid_tt",
        "uid_tt_ss",
        "ttwid",
    )
    LOGIN_HOST_SUFFIXES = ("douyin.com", "iesdouyin.com")
    QR_IMAGE_HOST_SUFFIXES = (
        "douyinpic.com",
        "byteimg.com",
        "douyincdn.com",
        "pstatp.com",
        "bytedance.com",
    )
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    COMMON_PARAMS = {
        "aid": "6383",
        "account_sdk_source": "sso",
        "device_platform": "web_app",
        "sdk_version": "2.2.5_beta.1",
        "language": "zh",
    }
    API_HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.douyin.com",
        "Referer": LOGIN_SERVICE_URL,
        "User-Agent": USER_AGENT,
    }
    CALLBACK_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Referer": LOGIN_SERVICE_URL,
        "User-Agent": USER_AGENT,
    }
    _HTML_VERIFICATION_MARKERS = (
        "captcha",
        "secsdk-captcha",
        "verifycenter",
        "verify_center",
        "人机验证",
        "安全验证",
        "设备验证",
    )
    _SESSION_KEY_PATTERN = re.compile(r"[A-Za-z0-9._~=-]{1,512}")
    _COOKIE_VALUE_PATTERN = re.compile(
        r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{1,4096}"
    )
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
                "Referer": self.LOGIN_SERVICE_URL,
            },
        )
        self._load_configured_ttwid(config)

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建二维码会话并下载受信任的官方二维码图片。"""
        await self._ensure_ttwid()
        payload = await self._get_payload(
            self.QR_GENERATE_URL,
            headers=self.API_HEADERS,
            params={
                **self.COMMON_PARAMS,
                "service": self.LOGIN_SERVICE_URL,
                "need_logo": "true",
                "need_short_url": "true",
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlatformLoginError("抖音暂时无法创建登录二维码，请稍后重试。")

        qr_url = str(data.get("qrcode") or "")
        session_key = str(data.get("token") or "")
        if (
            len(qr_url) > 2048
            or not self._is_trusted_https_url(qr_url, self.QR_IMAGE_HOST_SUFFIXES)
            or self._SESSION_KEY_PATTERN.fullmatch(session_key) is None
        ):
            raise PlatformLoginError("抖音返回了无效的二维码登录信息。")

        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=await self._download_qr_image(qr_url),
            expires_in_seconds=self.QR_EXPIRES_IN_SECONDS,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询扫码状态，成功后完成受控跳转并提取必要 Cookie。"""
        if self._SESSION_KEY_PATTERN.fullmatch(session_key) is None:
            raise PlatformLoginError("抖音登录会话无效，请重新发起登录。")

        payload = await self._get_payload(
            self.QR_POLL_URL,
            headers=self.API_HEADERS,
            params={
                **self.COMMON_PARAMS,
                "service": self.LOGIN_SERVICE_URL,
                "token": session_key,
                "need_logo": "true",
                "need_short_url": "true",
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlatformLoginError("抖音登录状态查询失败，请稍后重试。")

        status = str(data.get("status") or "").lower()
        if status in {"1", "new"}:
            return LoginPollResult(LoginPollState.WAITING)
        if status in {"2", "scanned"}:
            return LoginPollResult(LoginPollState.SCANNED)
        if status in {"4", "5", "expired"}:
            # 状态 4 会轮换二维码和令牌；私聊中不能替换已发送图片，要求重新发起。
            return LoginPollResult(LoginPollState.EXPIRED)
        if status == "3":
            redirect_url = str(data.get("redirect_url") or "")
            if not self._is_trusted_https_url(redirect_url, self.LOGIN_HOST_SUFFIXES):
                raise PlatformLoginError("抖音返回了无效的登录确认信息。")
            await self._complete_login_redirects(redirect_url)
            cookie_header = self._cookie_header()
            if not any(
                f"{name}=" in cookie_header for name in ("sessionid", "sessionid_ss")
            ):
                raise PlatformLoginError("抖音登录成功，但响应中缺少有效登录凭据。")
            return LoginPollResult(LoginPollState.SUCCESS, cookie_header)
        raise PlatformLoginError("抖音返回了无法识别的登录状态，请重新发起登录。")

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        payload = await self._get_payload(
            self.CURRENT_USER_URL,
            headers={**self.API_HEADERS, "Cookie": cookie_header},
            params=self.COMMON_PARAMS,
        )
        if payload.get("status_code") != 0:
            return None
        user = payload.get("user")
        if not isinstance(user, dict):
            return None
        user_id = str(user.get("uid") or user.get("short_id") or "").strip()
        display_name = str(user.get("nickname") or "").strip()
        if not user_id and not display_name:
            return None
        return PlatformUser(user_id=user_id, display_name=display_name)

    async def _ensure_ttwid(self) -> None:
        """通过配置或官方匿名注册流程准备二维码会话所需的 ttwid。"""
        if self._has_ttwid():
            return

        payload = await self._get_payload(
            self.TTWID_REGISTER_URL,
            method="POST",
            headers=self.API_HEADERS,
            json={
                "region": "cn",
                "aid": 6383,
                "needFid": False,
                "service": "www.douyin.com",
                "union": True,
                "fid": "",
            },
        )
        if self._has_ttwid():
            return

        redirect_url = str(payload.get("redirect_url") or "")
        if not self._is_trusted_https_url(redirect_url, self.LOGIN_HOST_SUFFIXES):
            raise PlatformLoginError("抖音匿名会话返回了不安全的回调地址。")
        await self._complete_ttwid_callback(redirect_url)
        if not self._has_ttwid():
            raise PlatformLoginError("抖音匿名会话初始化失败，请稍后重试。")

    async def _complete_ttwid_callback(self, callback_url: str) -> None:
        """在抖音受信任域内逐跳完成匿名会话回调。"""
        current_url = callback_url
        for _ in range(self.MAX_LOGIN_REDIRECTS + 1):
            if not self._is_trusted_https_url(current_url, self.LOGIN_HOST_SUFFIXES):
                raise PlatformLoginError("抖音匿名会话返回了不安全的重定向。")
            try:
                async with self._client.stream(
                    "GET",
                    current_url,
                    headers=self.CALLBACK_HEADERS,
                    follow_redirects=False,
                ) as response:
                    content = await read_login_response_body(
                        response,
                        limit=self.MAX_RESPONSE_BYTES,
                        platform=self.display_name,
                    )
                    if response.status_code not in self._REDIRECT_STATUS_CODES:
                        response.raise_for_status()
                        if self._has_ttwid():
                            return
                        if self._is_html_response(response, content):
                            self._raise_for_html_response(content)
                        return
                    location = response.headers.get("Location", "")
            except PlatformLoginError:
                raise
            except httpx.HTTPError as exc:
                raise PlatformLoginError(
                    "抖音匿名会话初始化请求失败，请稍后重试。"
                ) from exc
            if not location or len(location) > 2048:
                raise PlatformLoginError("抖音匿名会话返回了不安全的重定向。")
            current_url = urljoin(current_url, location)
        raise PlatformLoginError("抖音匿名会话重定向次数超过安全限制。")

    async def _get_payload(
        self,
        url: str,
        *,
        method: str = "GET",
        **kwargs,
    ) -> dict:
        try:
            content, content_type = await self._read_limited_response(
                url,
                method=method,
                limit=self.MAX_RESPONSE_BYTES,
                **kwargs,
            )
            if self._is_html_content(content_type, content):
                self._raise_for_html_response(content)
            payload = json.loads(content)
        except PlatformLoginError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformLoginError("抖音登录服务请求失败，请稍后重试。") from exc
        if not isinstance(payload, dict):
            raise PlatformLoginError("抖音登录服务返回了无效响应。")
        if self._requires_verification(payload):
            raise self._verification_error()
        return payload

    async def _download_qr_image(self, url: str) -> bytes:
        try:
            content, content_type = await self._read_limited_response(
                url,
                limit=self.MAX_QR_IMAGE_BYTES,
            )
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError("抖音登录二维码获取失败，请稍后重试。") from exc

        supported_type = content_type.split(";", 1)[0].strip() in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }
        supported_signature = (
            content.startswith(b"\x89PNG\r\n\x1a\n")
            or content.startswith(b"\xff\xd8\xff")
            or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
        )
        if not supported_type or not supported_signature:
            raise PlatformLoginError("抖音返回了无效的登录二维码图片。")
        return content

    async def _read_limited_response(
        self,
        url: str,
        *,
        method: str = "GET",
        limit: int,
        **kwargs,
    ) -> tuple[bytes, str]:
        async with self._client.stream(
            method,
            url,
            follow_redirects=False,
            **kwargs,
        ) as response:
            if response.is_redirect:
                raise PlatformLoginError("抖音登录服务返回了不安全的重定向。")
            response.raise_for_status()
            content = await read_login_response_body(
                response,
                limit=limit,
                platform=self.display_name,
            )
            return content, response.headers.get("Content-Type", "").lower()

    async def _complete_login_redirects(self, redirect_url: str) -> None:
        current_url = redirect_url
        for _ in range(self.MAX_LOGIN_REDIRECTS + 1):
            if not self._is_trusted_https_url(current_url, self.LOGIN_HOST_SUFFIXES):
                raise PlatformLoginError("抖音返回了无效的登录确认信息。")
            try:
                async with self._client.stream("GET", current_url) as response:
                    if response.status_code not in self._REDIRECT_STATUS_CODES:
                        response.raise_for_status()
                        return
                    location = response.headers.get("Location", "")
            except httpx.HTTPError as exc:
                raise PlatformLoginError("抖音登录确认请求失败，请稍后重试。") from exc
            if not location:
                raise PlatformLoginError("抖音返回了无效的登录确认信息。")
            if len(location) > 2048:
                raise PlatformLoginError("抖音返回了无效的登录确认信息。")
            current_url = urljoin(current_url, location)
        raise PlatformLoginError("抖音登录确认重定向次数超过安全限制。")

    def _cookie_header(self) -> str:
        return cookie_header_from_jar(
            self._client.cookies.jar,
            self.COOKIE_NAMES,
            domain_allowed=self._is_trusted_cookie_domain,
            value_allowed=self._is_safe_cookie_value,
        )

    def _load_configured_ttwid(self, config) -> None:
        """仅从配置中复用匿名 ttwid，不继承既有账号登录 Cookie。"""
        configured_cookies = parse_cookie_header(
            cookie_config_value(config, self.cookie_config_key)
        )
        for name, value in configured_cookies:
            if name != "ttwid" or not self._is_safe_cookie_value(value):
                continue
            self._client.cookies.set(
                "ttwid",
                value,
                domain=".douyin.com",
                path="/",
            )
            return

    def _has_ttwid(self) -> bool:
        return any(
            cookie.name == "ttwid"
            and bool(cookie.value)
            and self._is_safe_cookie_value(cookie.value)
            and self._is_trusted_cookie_domain(
                str(cookie.domain or "").lstrip(".").lower()
            )
            for cookie in self._client.cookies.jar
        )

    @staticmethod
    def _is_safe_cookie_value(value: str) -> bool:
        return DouyinLoginProvider._COOKIE_VALUE_PATTERN.fullmatch(value) is not None

    @classmethod
    def _is_trusted_cookie_domain(cls, domain: str) -> bool:
        return host_matches(domain, cls.LOGIN_HOST_SUFFIXES)

    @staticmethod
    def _requires_verification(payload: dict) -> bool:
        for container in (payload, payload.get("data")):
            if not isinstance(container, dict):
                continue
            if any(
                container.get(name)
                for name in (
                    "captcha",
                    "verify_ticket",
                    "verify_center_decision_conf",
                )
            ):
                return True
        return False

    @classmethod
    def _raise_for_html_response(cls, content: bytes) -> None:
        text = (
            content[: cls.MAX_RESPONSE_BYTES].decode("utf-8", errors="ignore").lower()
        )
        if any(marker in text for marker in cls._HTML_VERIFICATION_MARKERS):
            raise cls._verification_error()
        raise PlatformLoginError("抖音登录服务返回了无效响应。")

    @staticmethod
    def _is_html_content(content_type: str, content: bytes) -> bool:
        return "text/html" in content_type or content.lstrip().startswith(b"<")

    @classmethod
    def _is_html_response(cls, response: httpx.Response, content: bytes) -> bool:
        return cls._is_html_content(
            response.headers.get("Content-Type", "").lower(),
            content,
        )

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "抖音登录触发了平台人机或设备验证，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )

    @staticmethod
    def _is_trusted_https_url(url: str, host_suffixes: tuple[str, ...]) -> bool:
        return is_trusted_https_url(url, host_suffixes)
