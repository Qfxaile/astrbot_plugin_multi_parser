"""定义平台登录流程使用的公共契约。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


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
