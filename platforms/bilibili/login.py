"""实现 B站二维码登录与 Cookie 提取。"""

import json
import re

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


class BilibiliLoginProvider(HTTPPlatformLoginProvider):
    """通过 B站官方网页二维码接口建立管理员登录态。"""

    display_name = "B站"
    cookie_config_key = "bilibili_cookies"
    QR_GENERATE_URL = (
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    )
    QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    CURRENT_USER_URL = "https://api.bilibili.com/x/web-interface/nav"
    QR_EXPIRES_IN_SECONDS = 180
    MAX_RESPONSE_BYTES = 64 * 1024
    COOKIE_NAMES = (
        "SESSDATA",
        "bili_jct",
        "DedeUserID",
        "DedeUserID__ckMd5",
        "sid",
        "buvid3",
        "buvid4",
    )
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
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
            headers={
                "User-Agent": self.USER_AGENT,
                "Referer": "https://www.bilibili.com/",
            },
        )

    async def create_qr_challenge(self) -> QRLoginChallenge:
        """请求 B站二维码并在内存中渲染为 PNG。"""
        payload = await self._get_payload(self.QR_GENERATE_URL)
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            raise PlatformLoginError("B站暂时无法创建登录二维码，请稍后重试。")

        login_url = str(data.get("url") or "")
        session_key = str(data.get("qrcode_key") or "")
        if (
            len(login_url) > 2048
            or not self._is_trusted_login_url(login_url)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", session_key) is None
        ):
            raise PlatformLoginError("B站返回了无效的二维码登录信息。")

        return QRLoginChallenge(
            session_key=session_key,
            image_bytes=render_login_qr_png(login_url),
            expires_in_seconds=self.QR_EXPIRES_IN_SECONDS,
        )

    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询扫码状态，成功时从同一 HTTP 会话提取必要 Cookie。"""
        payload = await self._get_payload(
            self.QR_POLL_URL,
            params={"qrcode_key": session_key},
        )
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            raise PlatformLoginError("B站登录状态查询失败，请稍后重试。")

        status_code = data.get("code")
        if status_code == 86101:
            return LoginPollResult(LoginPollState.WAITING)
        if status_code == 86090:
            return LoginPollResult(LoginPollState.SCANNED)
        if status_code == 86038:
            return LoginPollResult(LoginPollState.EXPIRED)
        if status_code == 0:
            cookie_header = self._cookie_header()
            if "SESSDATA=" not in cookie_header:
                raise PlatformLoginError("B站登录成功，但响应中缺少有效登录凭据。")
            return LoginPollResult(LoginPollState.SUCCESS, cookie_header)
        raise PlatformLoginError("B站返回了无法识别的登录状态，请重新发起登录。")

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        payload = await self._get_payload(
            self.CURRENT_USER_URL,
            headers={"Cookie": cookie_header},
        )
        data = payload.get("data")
        if payload.get("code") != 0 or not isinstance(data, dict):
            return None
        if data.get("isLogin") is False:
            return None
        user_id = str(data.get("mid") or "").strip()
        display_name = str(data.get("uname") or "").strip()
        if not user_id and not display_name:
            return None
        return PlatformUser(user_id=user_id, display_name=display_name)

    async def _get_payload(self, url: str, **kwargs) -> dict:
        try:
            async with self._client.stream("GET", url, **kwargs) as response:
                response.raise_for_status()
                content = await read_login_response_body(
                    response,
                    limit=self.MAX_RESPONSE_BYTES,
                    platform=self.display_name,
                )
            payload = json.loads(content)
        except PlatformLoginError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise PlatformLoginError("B站登录服务请求失败，请稍后重试。") from exc
        if not isinstance(payload, dict):
            raise PlatformLoginError("B站登录服务返回了无效响应。")
        return payload

    def _cookie_header(self) -> str:
        return cookie_header_from_jar(
            self._client.cookies.jar,
            self.COOKIE_NAMES,
            domain_allowed=lambda domain: host_matches(domain, ("bilibili.com",)),
        )

    @staticmethod
    def _is_trusted_login_url(url: str) -> bool:
        return is_trusted_https_url(url, ("bilibili.com",))
