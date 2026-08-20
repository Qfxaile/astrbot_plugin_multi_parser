"""实现小黑盒 App 二维码登录与最小登录凭据提取。"""

import json
import re
from collections.abc import Mapping
from urllib.parse import parse_qs, urlsplit

import httpx

from ...core.http import is_safe_cookie_value, is_trusted_https_url, parse_cookie_header
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
from .signing import RequestSigner


class XiaoheiheLoginProvider(HTTPPlatformLoginProvider):
    """通过小黑盒官网原生二维码接口建立管理员登录态。"""

    name = "xiaoheihe"
    display_name = "小黑盒"
    cookie_config_key = "xiaoheihe_cookies"
    API_ORIGIN = "https://api.xiaoheihe.cn"
    QR_CREATE_PATH = "/account/get_qrcode_url/"
    QR_POLL_PATH = "/account/qr_state/"
    RESTORE_LOGIN_PATH = "/account/restore_login"
    QR_LOGIN_PATH = "/account/qr_login/"
    QR_CREATE_URL = f"{API_ORIGIN}{QR_CREATE_PATH}"
    QR_POLL_URL = f"{API_ORIGIN}{QR_POLL_PATH}"
    RESTORE_LOGIN_URL = f"{API_ORIGIN}{RESTORE_LOGIN_PATH}"
    QR_EXPIRES_IN_SECONDS = 120
    MAX_RESPONSE_BYTES = 64 * 1024
    COOKIE_NAMES = ("pkey", "x_xhh_tokenid", "heybox_id")
    REQUIRED_COOKIE_NAMES = ("pkey", "heybox_id")
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    COMMON_PARAMS = {
        "app": "web",
        "os_type": "web",
        "x_app": "heybox_website",
        "x_client_type": "web",
        "x_os_type": "Windows",
        "x_client_version": "",
        "client_type": "web",
        "web_version": "3.0",
        "version": "999.0.4",
    }
    _SESSION_KEY_PATTERN = re.compile(r"[A-Za-z0-9._~=-]{1,512}")
    _VERIFICATION_MARKERS = (
        "captcha",
        "challenge",
        "risk control",
        "滑块",
        "人机验证",
        "设备验证",
        "安全验证",
        "访问风险",
    )

    def __init__(
        self,
        config,
        *,
        client: httpx.AsyncClient | None = None,
        signer: RequestSigner | None = None,
    ) -> None:
        super().__init__(
            config,
            client=client,
            follow_redirects=False,
            headers={
                "User-Agent": self.USER_AGENT,
                "Origin": "https://xiaoheihe.cn",
                "Referer": "https://xiaoheihe.cn/",
            },
        )
        self._signer = signer or RequestSigner()

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建小黑盒二维码，并在内存中渲染官方登录链接。"""
        payload = await self._request_payload(
            self.QR_CREATE_URL,
            self.QR_CREATE_PATH,
        )
        result = payload.get("result")
        if payload.get("status") != "ok" or not isinstance(result, Mapping):
            raise PlatformLoginError("小黑盒暂时无法创建登录二维码，请稍后重试。")

        qr_url = str(result.get("qr_url") or "")
        session_key = self._session_key_from_qr_url(qr_url)
        if session_key is None:
            raise PlatformLoginError("小黑盒返回了无效的二维码登录信息。")

        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=render_login_qr_png(qr_url),
            expires_in_seconds=self._expires_in_seconds(result.get("expire")),
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询小黑盒 App 扫码状态，成功后提取账号登录凭据。"""
        if self._SESSION_KEY_PATTERN.fullmatch(session_key) is None:
            raise PlatformLoginError("小黑盒登录会话无效，请重新发起登录。")

        payload = await self._request_payload(
            self.QR_POLL_URL,
            self.QR_POLL_PATH,
            extra_params={"qr": session_key},
        )
        if str(payload.get("status") or "").lower() == "need_google_check":
            raise PlatformLoginError(
                "小黑盒账号需要完成二次验证，"
                "当前私聊流程无法继续，请在官方客户端完成后重试。"
            )

        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise PlatformLoginError("小黑盒登录状态查询失败，请稍后重试。")
        state = str(result.get("error") or "").lower()
        if state == "wait":
            return LoginPollResult(LoginPollState.WAITING)
        if state == "ready":
            return LoginPollResult(LoginPollState.SCANNED)
        if state == "ok":
            cookie_header = self._cookie_header(result)
            if not self._has_required_credentials(cookie_header):
                # 官网同样会在二维码确认结果不含完整账号态时调用
                # restore_login，依靠当前客户端会话取回最终登录凭据。
                restored_result = await self._restore_login()
                cookie_header = self._cookie_header(result, restored_result)
            if not self._has_required_credentials(cookie_header):
                raise PlatformLoginError("小黑盒登录成功，但响应中缺少有效登录凭据。")
            return LoginPollResult(LoginPollState.SUCCESS, cookie_header)
        return LoginPollResult(LoginPollState.EXPIRED)

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        credentials = dict(parse_cookie_header(cookie_header))
        user_id = str(credentials.get("heybox_id") or "").strip()
        if not self._is_safe_cookie_value(user_id):
            return None
        return PlatformUser(user_id=user_id)

    async def _request_payload(
        self,
        url: str,
        path: str,
        *,
        extra_params: Mapping[str, str] | None = None,
    ) -> dict:
        params = dict(self.COMMON_PARAMS)
        if extra_params:
            params.update(extra_params)
        # hkey 与路径、时间戳和 nonce 绑定，每次轮询都必须重新生成。
        params.update(self._signer.sign_path(path))
        try:
            async with self._client.stream(
                "GET",
                url,
                params=params,
                follow_redirects=False,
            ) as response:
                if response.is_redirect:
                    raise PlatformLoginError("小黑盒登录服务返回了不安全的重定向。")
                content = await read_login_response_body(
                    response,
                    limit=self.MAX_RESPONSE_BYTES,
                    platform=self.display_name,
                )
                content_type = response.headers.get("Content-Type", "").lower()
                if response.status_code in {401, 403, 429}:
                    raise self._verification_error()
                response.raise_for_status()
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError("小黑盒登录服务请求失败，请稍后重试。") from exc

        if "text/html" in content_type or content.lstrip().startswith(b"<"):
            raise self._verification_error()
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlatformLoginError("小黑盒登录服务返回了无效响应。") from exc
        if not isinstance(payload, dict):
            raise PlatformLoginError("小黑盒登录服务返回了无效响应。")
        if self._requires_verification(payload):
            raise self._verification_error()
        return payload

    async def _restore_login(self) -> Mapping:
        """按官网流程从当前二维码会话恢复完整账号登录态。"""
        payload = await self._request_payload(
            self.RESTORE_LOGIN_URL,
            self.RESTORE_LOGIN_PATH,
        )
        status = str(payload.get("status") or "").lower()
        if status == "need_google_check":
            raise PlatformLoginError(
                "小黑盒账号需要完成二次验证，"
                "当前私聊流程无法继续，请在官方客户端完成后重试。"
            )
        result = payload.get("result")
        if status != "ok" or not isinstance(result, Mapping):
            raise PlatformLoginError("小黑盒登录状态恢复失败，请重新发起登录。")
        return result

    @classmethod
    def _session_key_from_qr_url(cls, qr_url: str) -> str | None:
        if len(qr_url) > 2048:
            return None
        if not is_trusted_https_url(qr_url, ("api.xiaoheihe.cn",)):
            return None
        try:
            parsed = urlsplit(qr_url)
        except ValueError:
            return None
        if (
            parsed.hostname or ""
        ).lower() != "api.xiaoheihe.cn" or parsed.path != cls.QR_LOGIN_PATH:
            return None
        values = parse_qs(parsed.query, keep_blank_values=True).get("qr", [])
        if len(values) != 1 or cls._SESSION_KEY_PATTERN.fullmatch(values[0]) is None:
            return None
        return values[0]

    @classmethod
    def _cookie_header(cls, *results: Mapping) -> str:
        credentials: dict[str, str] = {}
        for result in results:
            pkey = result.get("pkey") or result.get("user_pkey")
            if cls._is_safe_cookie_value(pkey):
                credentials["pkey"] = str(pkey)

            token_id = result.get("x_xhh_tokenid")
            if cls._is_safe_cookie_value(token_id):
                credentials["x_xhh_tokenid"] = str(token_id)

            profile = result.get("profile")
            account_detail = result.get("account_detail")
            heybox_id = result.get("heybox_id") or result.get("user_heybox_id")
            if not cls._is_safe_cookie_value(heybox_id) and isinstance(
                profile, Mapping
            ):
                heybox_id = profile.get("heybox_id")
            if not cls._is_safe_cookie_value(heybox_id) and isinstance(
                account_detail, Mapping
            ):
                heybox_id = account_detail.get("userid")
            if cls._is_safe_cookie_value(heybox_id):
                credentials["heybox_id"] = str(heybox_id)

        return "; ".join(
            f"{name}={credentials[name]}"
            for name in cls.COOKIE_NAMES
            if name in credentials
        )

    @classmethod
    def _has_required_credentials(cls, cookie_header: str) -> bool:
        return all(f"{name}=" in cookie_header for name in cls.REQUIRED_COOKIE_NAMES)

    @staticmethod
    def _is_safe_cookie_value(value: object) -> bool:
        return is_safe_cookie_value(value)

    @classmethod
    def _requires_verification(cls, payload: Mapping) -> bool:
        containers = [payload]
        result = payload.get("result")
        if isinstance(result, Mapping):
            containers.append(result)
        for container in containers:
            if any(
                container.get(name)
                for name in (
                    "captcha",
                    "challenge",
                    "need_captcha",
                    "need_device_verify",
                    "need_verification",
                )
            ):
                return True
            for name in ("error", "message", "msg"):
                value = container.get(name)
                if isinstance(value, str) and any(
                    marker in value.lower() for marker in cls._VERIFICATION_MARKERS
                ):
                    return True
        return False

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "小黑盒登录触发了平台人机或设备验证，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )

    @classmethod
    def _expires_in_seconds(cls, value: object) -> int:
        try:
            expires_in = int(value)
        except (TypeError, ValueError):
            return cls.QR_EXPIRES_IN_SECONDS
        if 1 <= expires_in <= 600:
            return expires_in
        return cls.QR_EXPIRES_IN_SECONDS
