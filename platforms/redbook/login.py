"""实现小红书二维码登录与最小 Cookie 提取。"""

import json
import re
import secrets
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

import httpx
import qrcode

from ...core.authentication import (
    LoginPollResult,
    LoginPollState,
    PlatformLoginError,
    PlatformLoginProvider,
    PlatformUser,
    QRLoginChallenge,
)
from ...core.http import cookie_config_value, parse_cookie_header, request_timeout
from .signing import RedBookRequestSigner, XhshowRequestSigner


@dataclass(frozen=True)
class _QRSession:
    """保存仅在登录 Provider 内存中使用的平台会话字段。"""

    qr_id: str
    code: str


class RedBookLoginProvider(PlatformLoginProvider):
    """通过小红书官方 Web 二维码接口建立管理员登录态。"""

    display_name = "小红书"
    cookie_config_key = "redbook_cookies"
    BOOTSTRAP_URL = "https://www.xiaohongshu.com/explore"
    API_ORIGIN = "https://edith.xiaohongshu.com"
    QR_CREATE_URI = "/api/sns/web/v1/login/qrcode/create"
    QR_STATUS_URI = "/api/sns/web/v1/login/qrcode/status"
    QR_CREATE_URL = f"{API_ORIGIN}{QR_CREATE_URI}"
    QR_STATUS_URL = f"{API_ORIGIN}{QR_STATUS_URI}"
    CURRENT_USER_URI = "/api/sns/web/v1/user/selfinfo"
    CURRENT_USER_URL = f"{API_ORIGIN}{CURRENT_USER_URI}"
    QR_EXPIRES_IN_SECONDS = 60
    MAX_RESPONSE_BYTES = 64 * 1024
    MAX_BOOTSTRAP_PREFIX_BYTES = 64 * 1024
    LOGIN_HOST_SUFFIXES = ("xiaohongshu.com",)
    COOKIE_NAMES = ("a1", "web_session")
    RISK_CODES = frozenset({-13020, -13002, 461, 471})
    RISK_HTTP_STATUS_CODES = frozenset({412, 418, 429, 461, 471})
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    _PLATFORM_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~=-]{1,512}")

    def __init__(
        self,
        config,
        *,
        client: httpx.AsyncClient | None = None,
        signer: RedBookRequestSigner | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=request_timeout(config),
            follow_redirects=False,
            headers={
                "User-Agent": self.USER_AGENT,
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
            },
        )
        self._signer = signer or XhshowRequestSigner()
        self._sessions: dict[str, _QRSession] = {}
        configured_cookies = dict(
            parse_cookie_header(cookie_config_value(config, self.cookie_config_key))
        )
        for name in self.COOKIE_NAMES:
            value = configured_cookies.get(name, "")
            if self._valid_cookie_value(value):
                self._client.cookies.set(
                    name,
                    value,
                    domain=".xiaohongshu.com",
                    path="/",
                )

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建二维码会话，并只把本地随机会话键交给公共编排层。"""
        a1_value = await self._ensure_a1_cookie()
        payload = {"qr_type": 1}
        response = await self._signed_payload(
            "POST",
            self.QR_CREATE_URL,
            self.QR_CREATE_URI,
            a1_value,
            payload,
        )
        data = response.get("data")
        if response.get("code") != 0 or not isinstance(data, dict):
            raise PlatformLoginError("小红书暂时无法创建登录二维码，请稍后重试。")

        login_url = str(data.get("url") or "")
        qr_id = str(data.get("qr_id") or data.get("qrId") or "")
        code = str(data.get("code") or "")
        if (
            len(login_url) > 2048
            or not self._is_trusted_https_url(login_url)
            or self._PLATFORM_TOKEN_PATTERN.fullmatch(qr_id) is None
            or self._PLATFORM_TOKEN_PATTERN.fullmatch(code) is None
        ):
            raise PlatformLoginError("小红书返回了无效的二维码登录信息。")

        session_key = secrets.token_urlsafe(32)
        self._sessions[session_key] = _QRSession(qr_id=qr_id, code=code)
        expires_in = self._expires_in_seconds(data)
        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=self._render_qr_code(login_url),
            expires_in_seconds=expires_in,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询二维码状态，成功后提取小红书域的最小登录 Cookie。"""
        session = self._sessions.get(session_key)
        if session is None:
            raise PlatformLoginError("小红书登录会话无效，请重新发起登录。")
        a1_value = self._a1_cookie()
        if not a1_value:
            raise PlatformLoginError("小红书登录会话已失效，请重新发起登录。")

        params = {"qr_id": session.qr_id, "code": session.code}
        response = await self._signed_payload(
            "GET",
            self.QR_STATUS_URL,
            self.QR_STATUS_URI,
            a1_value,
            params,
        )
        data = response.get("data")
        if response.get("code") != 0 or not isinstance(data, dict):
            raise PlatformLoginError("小红书登录状态查询失败，请稍后重试。")

        try:
            status = int(data.get("code_status", data.get("codeStatus")))
        except (TypeError, ValueError):
            status = None
        if status == 0:
            return LoginPollResult(LoginPollState.WAITING)
        if status == 1:
            return LoginPollResult(LoginPollState.SCANNED)
        if status == 3:
            self._sessions.pop(session_key, None)
            return LoginPollResult(LoginPollState.EXPIRED)
        if status == 2:
            self._validate_success_url(data)
            cookie_header = self._cookie_header()
            self._sessions.pop(session_key, None)
            if not cookie_header:
                raise PlatformLoginError("小红书登录成功，但响应中缺少有效登录凭据。")
            return LoginPollResult(LoginPollState.SUCCESS, cookie_header)
        raise PlatformLoginError("小红书返回了无法识别的登录状态，请重新发起登录。")

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        configured = dict(parse_cookie_header(cookie_header))
        for name in self.COOKIE_NAMES:
            value = configured.get(name, "")
            if self._valid_cookie_value(value):
                self._client.cookies.set(
                    name,
                    value,
                    domain=".xiaohongshu.com",
                    path="/",
                )
        a1_value = self._a1_cookie()
        if not a1_value:
            return None
        payload = await self._signed_payload(
            "GET",
            self.CURRENT_USER_URL,
            self.CURRENT_USER_URI,
            a1_value,
            {},
        )
        if payload.get("code") != 0:
            return None
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        nested = tuple(
            value
            for value in (data.get("basic_info"), data.get("user"))
            if isinstance(value, dict)
        )
        candidates = (data, *nested)
        user_id = next(
            (
                str(item[key]).strip()
                for item in candidates
                for key in ("user_id", "userId", "id", "red_id", "redId")
                if item.get(key)
            ),
            "",
        )
        display_name = next(
            (
                str(item[key]).strip()
                for item in candidates
                for key in ("nickname", "nick_name", "nickName", "name")
                if item.get(key)
            ),
            "",
        )
        if not user_id and not display_name:
            return None
        return PlatformUser(user_id=user_id, display_name=display_name)

    async def close(self) -> None:
        """清除临时二维码会话并关闭由适配器创建的 HTTP 客户端。"""
        self._sessions.clear()
        if self._owns_client:
            await self._client.aclose()

    async def _ensure_a1_cookie(self) -> str:
        a1_value = self._a1_cookie()
        if a1_value:
            return a1_value
        try:
            async with self._client.stream(
                "GET",
                self.BOOTSTRAP_URL,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("Location", "")
                    if self._is_verification_url(location):
                        raise self._verification_error()
                    raise PlatformLoginError("小红书登录初始化返回了不安全的重定向。")
                if response.status_code in self.RISK_HTTP_STATUS_CODES:
                    raise self._verification_error()
                response.raise_for_status()
                prefix = bytearray()
                # 初始化只需要响应头中的官方 a1；最多读取固定前缀用于识别
                # 明确的验证页，随后主动关闭流，不缓冲完整页面。
                async for chunk in response.aiter_bytes():
                    remaining = self.MAX_BOOTSTRAP_PREFIX_BYTES - len(prefix)
                    if remaining <= 0:
                        break
                    prefix.extend(chunk[:remaining])
                    if len(prefix) >= self.MAX_BOOTSTRAP_PREFIX_BYTES:
                        break
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError("小红书登录初始化请求失败，请稍后重试。") from exc
        if self._looks_like_verification_page(bytes(prefix)):
            raise self._verification_error()
        a1_value = self._a1_cookie()
        if not a1_value:
            raise PlatformLoginError(
                "小红书二维码登录需要官网在真实浏览器环境设置的 a1，"
                "当前私聊流程无法自动初始化；"
                "请在小红书 Cookies 配置中保留 a1 后重试。"
            )
        return a1_value

    async def _signed_payload(
        self,
        method: str,
        url: str,
        uri: str,
        a1_value: str,
        payload: dict[str, object],
    ) -> dict:
        try:
            signed_headers = self._signer.sign(method, uri, a1_value, payload)
            kwargs: dict[str, object] = {
                "headers": signed_headers,
                "follow_redirects": False,
            }
            if method == "POST":
                kwargs["content"] = json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                signed_headers = {
                    **signed_headers,
                    "Content-Type": "application/json;charset=UTF-8",
                }
                kwargs["headers"] = signed_headers
            else:
                kwargs["params"] = payload
            content, content_type = await self._read_limited_response(
                method,
                url,
                **kwargs,
            )
            stripped = content.lstrip()
            if "text/html" in content_type or stripped.startswith(b"<"):
                raise self._verification_error()
            response = json.loads(content)
        except PlatformLoginError:
            raise
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise PlatformLoginError("小红书登录服务请求失败，请稍后重试。") from exc
        if not isinstance(response, dict):
            raise PlatformLoginError("小红书登录服务返回了无效响应。")
        if self._requires_verification(response):
            raise self._verification_error()
        return response

    async def _read_limited_response(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> tuple[bytes, str]:
        async with self._client.stream(method, url, **kwargs) as response:
            if response.is_redirect:
                if self._is_verification_url(response.headers.get("Location", "")):
                    raise self._verification_error()
                raise PlatformLoginError("小红书登录服务返回了不安全的重定向。")
            if response.status_code in self.RISK_HTTP_STATUS_CODES:
                raise self._verification_error()
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > self.MAX_RESPONSE_BYTES:
                    raise PlatformLoginError("小红书登录服务响应超过安全限制。")
                content.extend(chunk)
            return bytes(content), response.headers.get("Content-Type", "").lower()

    def _a1_cookie(self) -> str:
        for cookie in self._client.cookies.jar:
            domain = str(cookie.domain or "").lstrip(".").lower()
            if (
                cookie.name == "a1"
                and self._is_trusted_cookie_domain(domain)
                and self._valid_cookie_value(cookie.value)
            ):
                return str(cookie.value)
        return ""

    def _cookie_header(self) -> str:
        """返回后续重新登录所需的 ``a1`` 与账号 ``web_session``。"""
        values: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            domain = str(cookie.domain or "").lstrip(".").lower()
            if (
                cookie.name in self.COOKIE_NAMES
                and self._is_trusted_cookie_domain(domain)
                and self._valid_cookie_value(cookie.value)
            ):
                values[cookie.name] = str(cookie.value)
        if any(name not in values for name in self.COOKIE_NAMES):
            return ""
        return "; ".join(f"{name}={values[name]}" for name in self.COOKIE_NAMES)

    @classmethod
    def _validate_success_url(cls, data: dict) -> None:
        for field_name in ("redirect_url", "redirectUrl", "login_url", "loginUrl"):
            value = data.get(field_name)
            if value and not cls._is_trusted_https_url(str(value)):
                raise PlatformLoginError("小红书返回了无效的登录确认信息。")

    @classmethod
    def _requires_verification(cls, payload: object) -> bool:
        if isinstance(payload, dict):
            raw_code = payload.get("code")
            try:
                if int(raw_code) in cls.RISK_CODES:
                    return True
            except (TypeError, ValueError):
                pass
            for name, value in payload.items():
                normalized_name = str(name).lower()
                if value and any(
                    marker in normalized_name
                    for marker in ("captcha", "verify_ticket", "security_check")
                ):
                    return True
                if cls._requires_verification(value):
                    return True
        elif isinstance(payload, list):
            return any(cls._requires_verification(value) for value in payload)
        elif isinstance(payload, str):
            lowered = payload.lower()
            return any(
                marker in lowered
                for marker in (
                    "captcha",
                    "verify-center",
                    "security-check",
                    "人机验证",
                    "安全验证",
                    "设备验证",
                    "风控",
                )
            )
        return False

    @staticmethod
    def _looks_like_verification_page(content: bytes) -> bool:
        lowered = content.lower()
        return any(
            marker in lowered
            for marker in (
                b"/404/security-check",
                b'id="captcha"',
                b"verify-center-container",
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
            marker in path for marker in ("/404/security-check", "/captcha", "/verify")
        )

    @classmethod
    def _is_trusted_https_url(cls, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return False
        hostname = (parsed.hostname or "").lower()
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and port in {None, 443}
            and cls._is_trusted_cookie_domain(hostname)
        )

    @classmethod
    def _is_trusted_cookie_domain(cls, domain: str) -> bool:
        return any(
            domain == suffix or domain.endswith(f".{suffix}")
            for suffix in cls.LOGIN_HOST_SUFFIXES
        )

    @staticmethod
    def _valid_cookie_value(value: object) -> bool:
        text = str(value or "")
        return (
            bool(text)
            and len(text) <= 4096
            and not any(character in text for character in ("\r", "\n", ";"))
        )

    @classmethod
    def _expires_in_seconds(cls, data: dict) -> int:
        raw_value = data.get("expire", data.get("expires_in"))
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return cls.QR_EXPIRES_IN_SECONDS
        return min(max(value, 15), cls.QR_EXPIRES_IN_SECONDS)

    @staticmethod
    def _render_qr_code(value: str) -> bytes:
        qr_code = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=4,
        )
        qr_code.add_data(value)
        qr_code.make(fit=True)
        image = qr_code.make_image(fill_color="black", back_color="white")
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "小红书登录触发了平台人机、设备验证或风控，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )
