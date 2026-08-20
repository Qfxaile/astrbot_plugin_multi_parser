"""实现贴吧所使用的百度账号二维码登录与 Cookie 提取。"""

import json
import re
import time
import uuid
from collections.abc import Collection
from urllib.parse import urljoin, urlsplit

import httpx

from ...core.http import cookie_header_from_jar, is_trusted_https_url
from ...core.platform_login import (
    HTTPPlatformLoginProvider,
    LoginPollResult,
    LoginPollState,
    PlatformLoginError,
    PlatformUser,
    QRLoginChallenge,
    read_login_response_body,
)


class TiebaLoginProvider(HTTPPlatformLoginProvider):
    """通过百度官方 Web 二维码流程建立贴吧解析登录态。"""

    name = "tieba"
    display_name = "贴吧"
    cookie_config_key = "tieba_cookies"
    QR_GENERATE_URL = "https://passport.baidu.com/v2/api/getqrcode"
    QR_POLL_URL = "https://passport.baidu.com/channel/unicast"
    LOGIN_CONFIRM_URL = "https://passport.baidu.com/v3/login/main/qrbdusslogin"
    TIEBA_AUTH_URL = "https://passport.baidu.com/v3/login/api/auth/"
    LOGIN_RETURN_URL = "https://tieba.baidu.com/index.html"
    CURRENT_USER_URL = "https://tieba.baidu.com/f/user/json_userinfo"
    QR_EXPIRES_IN_SECONDS = 180
    MAX_RESPONSE_BYTES = 64 * 1024
    MAX_QR_IMAGE_BYTES = 512 * 1024
    MAX_LOGIN_REDIRECTS = 5
    COOKIE_NAMES = (
        "BAIDUID",
        "BAIDUID_BFESS",
        "BIDUPSID",
        "PSTM",
        "BDUSS",
        "BDUSS_BFESS",
        "STOKEN",
        "TIEBA_SID",
    )
    TIEBA_COOKIE_HOST = "tieba.baidu.com"
    LOGIN_HOSTS = frozenset({"passport.baidu.com", "tieba.baidu.com"})
    COOKIE_DOMAINS = frozenset({"baidu.com", "passport.baidu.com", "tieba.baidu.com"})
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    _SESSION_KEY_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}")
    _REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
    _VERIFICATION_MARKERS = (
        "百度安全验证",
        "请输入验证码",
        "安全检测",
        "seccaptcha.baidu.com",
        "bioc_options",
    )

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
                "Referer": "https://passport.baidu.com/",
            },
        )
        self._gid = ""

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建百度二维码会话并下载受信任的二维码图片。"""
        self._gid = self._new_gid()
        callback = self._callback_name()
        payload = await self._get_payload(
            self.QR_GENERATE_URL,
            callback=callback,
            params={
                "lp": "pc",
                "qrloginfrom": "pc",
                "tpl": "tb",
                "apiver": "v3",
                "gid": self._gid,
                "tt": self._timestamp_ms(),
                "callback": callback,
            },
        )
        if self._payload_requires_verification(payload):
            raise self._verification_error()
        if payload.get("errno") not in {0, "0"}:
            raise PlatformLoginError("贴吧暂时无法创建登录二维码，请稍后重试。")

        session_key = str(payload.get("sign") or "")
        image_value = str(payload.get("imgurl") or "")
        image_url = self._normalize_qr_image_url(image_value)
        if (
            self._SESSION_KEY_PATTERN.fullmatch(session_key) is None
            or len(image_url) > 2048
            or not self._is_trusted_https_url(image_url, {"passport.baidu.com"})
        ):
            raise PlatformLoginError("贴吧返回了无效的二维码登录信息。")

        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=await self._download_qr_image(image_url),
            expires_in_seconds=self.QR_EXPIRES_IN_SECONDS,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询扫码状态，成功后完成受控跳转并提取必要 Cookie。"""
        if not self._gid or self._SESSION_KEY_PATTERN.fullmatch(session_key) is None:
            raise PlatformLoginError("贴吧登录会话无效，请重新发起登录。")

        callback = self._callback_name()
        payload = await self._get_payload(
            self.QR_POLL_URL,
            callback=callback,
            params={
                "channel_id": session_key,
                "tpl": "tb",
                "apiver": "v3",
                "gid": self._gid,
                "tt": self._timestamp_ms(),
                "callback": callback,
            },
        )
        if self._payload_requires_verification(payload):
            raise self._verification_error()

        errno = payload.get("errno")
        if errno in {1, "1"}:
            return LoginPollResult(LoginPollState.WAITING)
        if errno in {2, "2"}:
            return LoginPollResult(LoginPollState.EXPIRED)
        if errno not in {0, "0"}:
            raise PlatformLoginError("贴吧返回了无法识别的登录状态，请重新发起登录。")

        channel_value = payload.get("channel_v")
        try:
            channel_payload = (
                json.loads(channel_value)
                if isinstance(channel_value, str)
                else channel_value
            )
        except (TypeError, json.JSONDecodeError) as exc:
            raise PlatformLoginError("贴吧登录状态查询返回了无效响应。") from exc
        if not isinstance(channel_payload, dict):
            raise PlatformLoginError("贴吧登录状态查询返回了无效响应。")
        if self._payload_requires_verification(channel_payload):
            raise self._verification_error()

        status = channel_payload.get("status")
        if status in {1, "1"}:
            return LoginPollResult(LoginPollState.SCANNED)
        if status in {2, "2"}:
            return LoginPollResult(LoginPollState.EXPIRED)
        if status not in {0, "0"}:
            raise PlatformLoginError("贴吧返回了无法识别的登录状态，请重新发起登录。")

        login_token = str(channel_payload.get("v") or "")
        if not self._is_safe_token(login_token):
            raise PlatformLoginError("贴吧登录成功，但确认信息无效。")
        await self._complete_login(login_token)
        await self._complete_tieba_authorization()
        cookie_header = self._cookie_header()
        has_baidu_session = any(
            f"{name}=" in cookie_header for name in ("BDUSS", "BDUSS_BFESS")
        )
        if not has_baidu_session or "STOKEN=" not in cookie_header:
            raise PlatformLoginError("贴吧登录成功，但响应中缺少有效登录凭据。")
        return LoginPollResult(LoginPollState.SUCCESS, cookie_header)

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        status_code, _, content = await self._read_limited_response(
            self.CURRENT_USER_URL,
            limit=self.MAX_RESPONSE_BYTES,
            headers={"Cookie": cookie_header},
        )
        if status_code != 200:
            return None
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None
        user_id = str(data.get("user_id") or data.get("id") or "").strip()
        display_name = str(
            data.get("user_name_show")
            or data.get("user_name_weak")
            or data.get("user_name")
            or ""
        ).strip()
        if not user_id and not display_name:
            return None
        return PlatformUser(user_id=user_id, display_name=display_name)

    async def close(self) -> None:
        """清除会话标识并关闭由适配器创建的 HTTP 客户端。"""
        self._gid = ""
        await super().close()

    async def _get_payload(
        self,
        url: str,
        *,
        callback: str,
        params: dict[str, str | int],
    ) -> dict:
        try:
            status_code, headers, content = await self._read_limited_response(
                url,
                limit=self.MAX_RESPONSE_BYTES,
                params=params,
            )
            if status_code in self._REDIRECT_STATUS_CODES:
                raise PlatformLoginError("贴吧登录服务返回了不安全的重定向。")
            if status_code >= 400:
                raise PlatformLoginError("贴吧登录服务请求失败，请稍后重试。")
            if self._is_verification_content(content):
                raise self._verification_error()
            payload = self._parse_json_or_jsonp(content, callback)
        except PlatformLoginError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformLoginError("贴吧登录服务请求失败，请稍后重试。") from exc
        if not isinstance(payload, dict):
            raise PlatformLoginError("贴吧登录服务返回了无效响应。")
        return payload

    async def _download_qr_image(self, url: str) -> bytes:
        try:
            status_code, headers, content = await self._read_limited_response(
                url,
                limit=self.MAX_QR_IMAGE_BYTES,
            )
            if status_code in self._REDIRECT_STATUS_CODES:
                raise PlatformLoginError("贴吧登录二维码返回了不安全的重定向。")
            if status_code >= 400:
                raise PlatformLoginError("贴吧登录二维码获取失败，请稍后重试。")
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError("贴吧登录二维码获取失败，请稍后重试。") from exc

        content_type = headers.get("Content-Type", "").split(";", 1)[0].strip()
        supported_type = content_type.lower() in {"image/jpeg", "image/png"}
        supported_signature = content.startswith(
            b"\x89PNG\r\n\x1a\n"
        ) or content.startswith(b"\xff\xd8\xff")
        if not supported_type or not supported_signature:
            raise PlatformLoginError("贴吧返回了无效的登录二维码图片。")
        return content

    async def _complete_login(self, login_token: str) -> None:
        """使用二维码一次性令牌完成百度账号登录。"""
        current_url = str(
            httpx.URL(
                self.LOGIN_CONFIRM_URL,
                params={
                    "bduss": login_token,
                    "u": self.LOGIN_RETURN_URL,
                    "loginVersion": "v4",
                    "qrcode": "1",
                    "tpl": "tb",
                    "apiver": "v3",
                    "tt": self._timestamp_ms(),
                },
            )
        )
        await self._follow_trusted_login_redirects(current_url)

    async def _complete_tieba_authorization(self) -> None:
        """把百度账号会话交换为贴吧可用的 STOKEN 登录态。"""
        current_url = str(
            httpx.URL(
                self.TIEBA_AUTH_URL,
                params={
                    "tpl": "tb",
                    "jump": "",
                    "return_type": "3",
                    "u": self.LOGIN_RETURN_URL,
                },
            )
        )
        await self._follow_trusted_login_redirects(current_url)

    async def _follow_trusted_login_redirects(self, current_url: str) -> None:
        """跟随百度账号与贴吧之间的受控 HTTPS 登录跳转。"""
        # 一次性令牌和业务授权令牌只能在两个官方域之间流转，每跳都重新校验。
        for _ in range(self.MAX_LOGIN_REDIRECTS + 1):
            if not self._is_trusted_https_url(current_url, self.LOGIN_HOSTS):
                raise PlatformLoginError("贴吧返回了无效的登录确认信息。")
            try:
                status_code, headers, content = await self._read_limited_response(
                    current_url,
                    limit=self.MAX_RESPONSE_BYTES,
                )
            except PlatformLoginError:
                raise
            except httpx.HTTPError as exc:
                raise PlatformLoginError("贴吧登录确认请求失败，请稍后重试。") from exc
            if self._is_verification_content(content):
                raise self._verification_error()
            if status_code not in self._REDIRECT_STATUS_CODES:
                if status_code >= 400:
                    raise PlatformLoginError("贴吧登录确认请求失败，请稍后重试。")
                return
            location = headers.get("Location", "")
            if not location or len(location) > 2048:
                raise PlatformLoginError("贴吧返回了无效的登录确认信息。")
            current_url = urljoin(current_url, location)
        raise PlatformLoginError("贴吧登录确认重定向次数超过安全限制。")

    async def _read_limited_response(
        self,
        url: str,
        *,
        limit: int,
        **kwargs,
    ) -> tuple[int, httpx.Headers, bytes]:
        async with self._client.stream(
            "GET",
            url,
            follow_redirects=False,
            **kwargs,
        ) as response:
            content = await read_login_response_body(
                response,
                limit=limit,
                platform=self.display_name,
            )
            return response.status_code, response.headers, content

    def _cookie_header(self) -> str:
        # 配置字符串不保留 Domain，因此只能持久化浏览器本来就会发往贴吧的
        # 条目，避免把 Passport 专用 Cookie 扁平化后扩大到贴吧请求。
        return cookie_header_from_jar(
            self._client.cookies.jar,
            self.COOKIE_NAMES,
            domain_allowed=self._cookie_applies_to_tieba,
        )

    @classmethod
    def _cookie_applies_to_tieba(cls, domain: str) -> bool:
        """判断 Cookie Domain 是否允许浏览器把该条目发送到贴吧。"""
        return domain in cls.COOKIE_DOMAINS and (
            domain == cls.TIEBA_COOKIE_HOST
            or cls.TIEBA_COOKIE_HOST.endswith(f".{domain}")
        )

    @classmethod
    def _is_verification_content(cls, content: bytes) -> bool:
        text = content.decode("utf-8", errors="ignore").lower()
        return any(marker.lower() in text for marker in cls._VERIFICATION_MARKERS)

    @staticmethod
    def _payload_requires_verification(payload: dict) -> bool:
        for container in (payload, payload.get("data")):
            if not isinstance(container, dict):
                continue
            lowered = {str(key).lower(): value for key, value in container.items()}
            if any(
                lowered.get(name)
                for name in (
                    "captcha",
                    "verifycode",
                    "verify_token",
                    "risk_token",
                )
            ):
                return True
        return False

    @staticmethod
    def _parse_json_or_jsonp(content: bytes, callback: str) -> dict:
        text = content.decode("utf-8").strip()
        if text.startswith("{"):
            return json.loads(text)
        prefix = f"{callback}("
        if not text.startswith(prefix):
            raise ValueError("unexpected JSONP callback")
        body = text[len(prefix) :].rstrip()
        if body.endswith(";"):
            body = body[:-1].rstrip()
        if not body.endswith(")"):
            raise ValueError("invalid JSONP payload")
        return json.loads(body[:-1])

    @staticmethod
    def _is_safe_token(value: str) -> bool:
        return (
            bool(value)
            and len(value) <= 2048
            and all(0x21 <= ord(character) <= 0x7E for character in value)
        )

    @staticmethod
    def _timestamp_ms() -> int:
        return int(time.time() * 1000)

    @staticmethod
    def _new_gid() -> str:
        """生成与百度 Web 登录页格式一致的 35 位会话标识。"""
        return str(uuid.uuid4()).upper()[1:]

    @classmethod
    def _callback_name(cls) -> str:
        return f"tangram_guid_{cls._timestamp_ms()}"

    @classmethod
    def _normalize_qr_image_url(cls, value: str) -> str:
        """兼容百度返回的绝对、协议相对和省略协议的官方图片地址。"""
        value = value.strip()
        if value.startswith("//"):
            return f"https:{value}"
        try:
            if urlsplit(value).scheme:
                return value
        except ValueError:
            return ""
        host_path = value.lstrip("/")
        if host_path.lower().startswith("passport.baidu.com/"):
            return f"https://{host_path}"
        return urljoin(cls.QR_GENERATE_URL, value)

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "贴吧登录触发了平台人机或设备验证，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )

    @staticmethod
    def _is_trusted_https_url(
        url: str,
        allowed_hosts: Collection[str],
    ) -> bool:
        if not is_trusted_https_url(url, allowed_hosts):
            return False
        return (urlsplit(url).hostname or "").lower() in allowed_hosts
