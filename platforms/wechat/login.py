"""实现微信扫码授权与腾讯元宝登录令牌提取。"""

import json
import re
import secrets
import string
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit

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
)


@dataclass
class _WeChatQRSession:
    """保存一次微信开放平台授权所需的内存态。"""

    uuid: str
    last_status: int | None = None


class _QRImageParser(HTMLParser):
    """从微信开放平台授权页提取二维码图片候选地址。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_urls: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "img":
            return
        attributes = dict(attrs)
        class_names = str(attributes.get("class") or "").split()
        if not any("qrcode" in name for name in class_names):
            return
        image_url = str(attributes.get("src") or "").strip()
        if image_url:
            self.image_urls.append(image_url)


class WeChatLoginProvider(HTTPPlatformLoginProvider):
    """通过微信开放平台扫码建立腾讯元宝解析登录态。"""

    display_name = "微信"
    cookie_config_key = "wechat_yuanbao_cookies"
    OAUTH_APP_ID = "wx12b75947931a04ec"
    OAUTH_URL = "https://open.weixin.qq.com/connect/qrconnect"
    QR_IMAGE_HOST = "open.weixin.qq.com"
    QR_POLL_URL = "https://long.open.weixin.qq.com/connect/l/qrconnect"
    CALLBACK_URL = "https://yuanbao.tencent.com/scan"
    YUANBAO_LOGIN_URL = "https://yuanbao.tencent.com/api/joint/login"
    QR_EXPIRES_IN_SECONDS = 300
    MAX_AUTH_PAGE_BYTES = 512 * 1024
    MAX_QR_IMAGE_BYTES = 512 * 1024
    MAX_POLL_RESPONSE_BYTES = 16 * 1024
    MAX_LOGIN_RESPONSE_BYTES = 128 * 1024
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    _UUID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
    _POLL_PATTERN = re.compile(
        rb"window\.wx_errcode\s*=\s*(\d+)\s*;\s*"
        rb"window\.wx_code\s*=\s*['\"]([^'\"]*)['\"]"
    )
    _AUTH_CODE_PATTERN = re.compile(rb"[A-Za-z0-9_-]{1,512}")
    _REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
    _RISK_MARKERS = (
        "captcha",
        "安全验证",
        "人机验证",
        "滑块验证",
        "设备验证",
        "访问异常",
        "操作频繁",
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
                "Accept-Language": "zh-CN,zh;q=0.9",
                "User-Agent": self.USER_AGENT,
            },
        )
        self._sessions: dict[str, _WeChatQRSession] = {}

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建微信开放平台二维码并下载受信任的二维码图片。"""
        nonce = self._new_nonce()
        redirect_uri = f"{self.CALLBACK_URL}?{urlencode({'nonce': nonce})}"
        try:
            content, content_type, status_code, _ = await self._read_limited_response(
                self.OAUTH_URL,
                limit=self.MAX_AUTH_PAGE_BYTES,
                params={
                    "appid": self.OAUTH_APP_ID,
                    "scope": "snsapi_login",
                    "redirect_uri": redirect_uri,
                    "state": "wechat_login",
                    "login_type": "jssdk",
                    "self_redirect": "false",
                    "style": "white",
                },
            )
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError(
                "微信登录服务请求失败，请稍后重试。"
            ) from exc

        if status_code in self._REDIRECT_STATUS_CODES:
            raise PlatformLoginError("微信登录服务返回了不安全的重定向。")
        if status_code >= 400:
            raise PlatformLoginError("微信暂时无法创建登录二维码，请稍后重试。")
        if "text/html" not in content_type and not content.lstrip().startswith(b"<"):
            raise PlatformLoginError("微信登录服务返回了无效响应。")

        qr_url, uuid = self._extract_qr_image(content)
        if not qr_url or not uuid:
            if self._contains_risk_marker(content):
                raise self._verification_error()
            raise PlatformLoginError("微信返回了无效的二维码登录信息。")

        image_bytes = await self._download_qr_image(qr_url)
        session_key = secrets.token_urlsafe(32)
        self._sessions[session_key] = _WeChatQRSession(uuid=uuid)
        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=image_bytes,
            expires_in_seconds=self.QR_EXPIRES_IN_SECONDS,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询扫码状态，成功后用授权码换取元宝最小登录令牌。"""
        session = self._sessions.get(session_key)
        if session is None:
            raise PlatformLoginError("微信登录会话无效，请重新发起登录。")

        params = {"uuid": session.uuid}
        if session.last_status is not None:
            params["last"] = str(session.last_status)
        try:
            content, _, status_code, _ = await self._read_limited_response(
                self.QR_POLL_URL,
                limit=self.MAX_POLL_RESPONSE_BYTES,
                params=params,
            )
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError(
                "微信登录状态查询失败，请稍后重试。"
            ) from exc

        if status_code in self._REDIRECT_STATUS_CODES or status_code >= 400:
            raise PlatformLoginError("微信登录状态查询失败，请稍后重试。")
        match = self._POLL_PATTERN.search(content)
        if match is None:
            if self._contains_risk_marker(content):
                raise self._verification_error()
            raise PlatformLoginError(
                "微信返回了无法识别的登录状态，请重新发起登录。"
            )

        poll_status = int(match.group(1))
        auth_code = match.group(2)
        if poll_status == 408:
            session.last_status = None
            return LoginPollResult(LoginPollState.WAITING)
        if poll_status == 404:
            session.last_status = poll_status
            return LoginPollResult(LoginPollState.SCANNED)
        if poll_status == 402:
            self._sessions.pop(session_key, None)
            return LoginPollResult(LoginPollState.EXPIRED)
        if poll_status == 403:
            self._sessions.pop(session_key, None)
            raise PlatformLoginError(
                "微信登录已在手机端取消，请重新发起登录。"
            )
        if poll_status == 405:
            if self._AUTH_CODE_PATTERN.fullmatch(auth_code) is None:
                raise PlatformLoginError("微信返回了无效的登录确认信息。")
            credential_header = await self._complete_yuanbao_login(
                auth_code.decode("ascii")
            )
            self._sessions.pop(session_key, None)
            return LoginPollResult(LoginPollState.SUCCESS, credential_header)

        raise PlatformLoginError(
            "微信返回了无法识别的登录状态，请重新发起登录。"
        )

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        credentials = dict(parse_cookie_header(cookie_header))
        user_id = self._safe_credential_value(credentials.get("yb_user_id"))
        if not user_id:
            return None
        return PlatformUser(user_id=user_id)

    async def close(self) -> None:
        """清理二维码会话并关闭由适配器创建的 HTTP 客户端。"""
        self._sessions.clear()
        await super().close()

    async def _download_qr_image(self, url: str) -> bytes:
        try:
            content, content_type, status_code, _ = await self._read_limited_response(
                url,
                limit=self.MAX_QR_IMAGE_BYTES,
            )
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError(
                "微信登录二维码获取失败，请稍后重试。"
            ) from exc
        if status_code in self._REDIRECT_STATUS_CODES:
            raise PlatformLoginError("微信登录二维码返回了不安全的重定向。")
        if status_code >= 400:
            raise PlatformLoginError("微信登录二维码获取失败，请稍后重试。")

        media_type = content_type.split(";", 1)[0].strip()
        supported_type = media_type in {"image/jpeg", "image/png"}
        supported_signature = content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(
            b"\xff\xd8\xff"
        )
        if not supported_type or not supported_signature:
            raise PlatformLoginError("微信返回了无效的登录二维码图片。")
        return content

    async def _complete_yuanbao_login(
        self,
        auth_code: str,
    ) -> str:
        # 当前元宝网页不会通过 /scan 写 Cookie，而是将微信授权码交给
        # /api/joint/login，并把响应令牌保存在浏览器 localStorage。
        try:
            async with self._client.stream(
                "POST",
                self.YUANBAO_LOGIN_URL,
                follow_redirects=False,
                json={
                    "type": "wx",
                    "jsCode": auth_code,
                    "appid": self.OAUTH_APP_ID,
                    "apiFeature": "team",
                },
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                    "Origin": "https://yuanbao.tencent.com",
                    "Referer": self.CALLBACK_URL,
                    "X-Source": "web",
                },
            ) as response:
                response_content = await read_login_response_body(
                    response,
                    limit=self.MAX_LOGIN_RESPONSE_BYTES,
                    platform=self.display_name,
                )
                status_code = response.status_code
        except PlatformLoginError:
            raise
        except httpx.HTTPError as exc:
            raise PlatformLoginError(
                "微信登录确认请求失败，请稍后重试。"
            ) from exc

        if status_code in self._REDIRECT_STATUS_CODES:
            raise PlatformLoginError("微信登录服务返回了不安全的重定向。")
        if status_code in {403, 429} or self._contains_risk_marker(response_content):
            raise self._verification_error()
        if status_code >= 400:
            raise PlatformLoginError("微信登录确认请求失败，请稍后重试。")

        try:
            payload = json.loads(response_content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlatformLoginError("微信返回了无效的登录确认信息。") from exc
        if not isinstance(payload, Mapping):
            raise PlatformLoginError("微信返回了无效的登录确认信息。")
        data = payload.get("data")
        credentials = data if isinstance(data, Mapping) else payload
        user_id = self._safe_credential_value(credentials.get("userId"))
        token = self._safe_credential_value(credentials.get("token"))
        if not user_id or not token:
            raise PlatformLoginError(
                "微信登录成功，但响应中缺少有效的腾讯元宝登录凭据。"
            )
        return f"yb_user_id={user_id}; yb_token={token}"

    async def _read_limited_response(
        self,
        url: str,
        *,
        limit: int,
        **kwargs,
    ) -> tuple[bytes, str, int, str]:
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
            return (
                content,
                response.headers.get("Content-Type", "").lower(),
                response.status_code,
                response.headers.get("Location", ""),
            )

    @classmethod
    def _extract_qr_image(cls, content: bytes) -> tuple[str, str]:
        parser = _QRImageParser()
        try:
            parser.feed(content.decode("utf-8", errors="replace"))
            parser.close()
        except ValueError:
            return "", ""
        for image_url in parser.image_urls:
            image_url = urljoin(cls.OAUTH_URL, image_url)
            if not is_trusted_https_url(image_url, (cls.QR_IMAGE_HOST,)):
                continue
            try:
                parsed = urlsplit(image_url)
            except ValueError:
                continue
            path_match = re.fullmatch(r"/connect/qrcode/([^/]+)", parsed.path)
            if (
                (parsed.hostname or "").lower() != cls.QR_IMAGE_HOST
                or path_match is None
            ):
                continue
            uuid = path_match.group(1)
            if cls._UUID_PATTERN.fullmatch(uuid) is not None:
                return image_url, uuid
        return "", ""

    @staticmethod
    def _safe_credential_value(value: object) -> str:
        return str(value) if is_safe_cookie_value(value) else ""

    @classmethod
    def _contains_risk_marker(cls, content: bytes) -> bool:
        text = content.decode("utf-8", errors="ignore").lower()
        return any(marker in text for marker in cls._RISK_MARKERS)

    @staticmethod
    def _verification_error() -> PlatformLoginError:
        return PlatformLoginError(
            "微信登录触发了平台人机、设备验证或风控，"
            "当前私聊流程无法继续，请稍后重试或手工配置 Cookies。"
        )

    @staticmethod
    def _new_nonce() -> str:
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(16))
