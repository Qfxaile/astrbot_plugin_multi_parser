"""定义平台登录流程使用的公共契约。"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from io import BytesIO

import httpx
import qrcode

from .http import http_client_proxy_options, request_timeout


class PlatformLoginError(ValueError):
    """表示可安全展示给管理员的平台登录错误。"""


class LoginPollState(str, Enum):
    """表示二维码登录的一次轮询结果。"""

    WAITING = "waiting"
    SCANNED = "scanned"
    SUCCESS = "success"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PlatformUser:
    """包含可安全展示的最小平台账号信息。"""

    user_id: str = ""
    display_name: str = ""


@dataclass(frozen=True)
class QRLoginChallenge:
    """包含待发送二维码和平台侧会话标识。"""

    session_key: str
    image_bytes: bytes
    expires_in_seconds: int


@dataclass(frozen=True)
class LoginPollResult:
    """包含二维码状态，以及成功后需要持久化的 Cookie。"""

    state: LoginPollState
    cookie_header: str = ""


class PlatformLoginProvider(ABC):
    """声明单个平台通过私聊完成登录所需的最小能力。"""

    name = ""
    display_name = ""
    qr_scanner_name = ""
    cookie_config_key = ""
    sms_fallback_available = False

    @abstractmethod
    async def create_qr_challenge(self) -> QRLoginChallenge:
        """创建二维码登录会话。"""

    @abstractmethod
    async def poll_qr_status(self, session_key: str) -> LoginPollResult:
        """轮询二维码状态。"""

    async def get_current_user(self, cookie_header: str) -> PlatformUser | None:
        """使用指定登录凭据查询当前账号；不可用时返回 ``None``。"""
        return None

    @abstractmethod
    async def close(self) -> None:
        """释放登录期间持有的网络资源。"""


class HTTPPlatformLoginProvider(PlatformLoginProvider):
    """管理登录适配器使用的 HTTP 客户端所有权。"""

    def __init__(
        self,
        config: Mapping[str, object],
        *,
        client: httpx.AsyncClient | None = None,
        **client_options,
    ) -> None:
        self._owns_client = client is None
        if client is not None:
            self._client = client
        else:
            self._client = httpx.AsyncClient(
                timeout=request_timeout(config),
                **http_client_proxy_options(config, self.name),
                **client_options,
            )

    async def close(self) -> None:
        """关闭由登录适配器创建的 HTTP 客户端。"""
        if self._owns_client:
            await self._client.aclose()


async def read_login_response_body(
    response: httpx.Response,
    *,
    limit: int,
    platform: str,
) -> bytes:
    """限长读取登录响应，且不在异常中保留响应正文。"""
    content = bytearray()
    async for chunk in response.aiter_bytes():
        if len(content) + len(chunk) > limit:
            raise PlatformLoginError(f"{platform}登录服务响应超过安全限制。")
        content.extend(chunk)
    return bytes(content)


def render_login_qr_png(value: str) -> bytes:
    """在内存中将登录地址渲染为 PNG 二维码。"""
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
