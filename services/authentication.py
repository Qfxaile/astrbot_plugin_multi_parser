"""协调管理员私聊中的平台登录、取消和凭据持久化。"""

import asyncio
from collections.abc import Callable, Mapping
from functools import partial

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain

from ..core.http import parse_cookie_header
from ..core.platform_login import (
    LoginPollState,
    PlatformLoginError,
    PlatformLoginProvider,
    PlatformUser,
)
from ..platforms.registry import login_platforms
from .cookie_store import CookieStore
from .login_messages import LoginMessageFormatter
from .login_polling import QRLoginPoller
from .login_sessions import LoginAttempt, LoginSessionRegistry
from .login_status import LoginStatusService

ProviderFactory = Callable[[], PlatformLoginProvider]


class AuthenticationService:
    """管理插件级平台账号，并保证登录流程不会跨私聊串联。"""

    POLL_INTERVAL_SECONDS = 2.0

    def __init__(
        self,
        config,
        *,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
    ) -> None:
        self.config = config
        self.cookie_store = CookieStore(config)
        self._provider_factories = dict(
            provider_factories
            or {
                registration.login_provider_type.display_name: partial(
                    registration.login_provider_type,
                    self.config,
                )
                for registration in login_platforms()
            }
        )
        self._cookie_keys = {
            registration.login_provider_type.display_name: (
                registration.login_provider_type.cookie_config_key
            )
            for registration in login_platforms()
        }
        self._sessions = LoginSessionRegistry()
        self._poller = QRLoginPoller(self.POLL_INTERVAL_SECONDS)
        self._status_service = LoginStatusService(
            self.cookie_store,
            self._provider_factories,
            self._cookie_keys,
            self._get_current_user,
        )

    @property
    def supported_platforms(self) -> tuple[str, ...]:
        """返回当前已实现登录的平台中文名。"""
        return tuple(self._provider_factories)

    async def login(self, event: AstrMessageEvent, platform_name: str) -> str | None:
        """发送二维码并等待平台确认，成功后保存 Cookie。

        参数:
            event: 发起登录的管理员私聊事件。
            platform_name: 用户输入的平台中文名。

        返回:
            可直接回复管理员的结果；由取消命令结束时返回 ``None``。
        """
        platform_name = platform_name.strip()
        factory = self._provider_factories.get(platform_name)
        if factory is None:
            return self._unsupported_platform_message(platform_name)

        provider = factory()
        attempt = LoginAttempt(
            session_id=self._session_id(event),
            provider=provider,
            cancel_event=asyncio.Event(),
        )
        if not await self._sessions.register(platform_name, attempt):
            await provider.close()
            return f"{platform_name}已有登录流程正在进行，请先取消或等待结束。"

        try:
            challenge = await provider.create_qr_challenge()
            await event.send(
                MessageChain(
                    [
                        Plain(
                            f"请使用{provider.qr_scanner_name or platform_name}"
                            "客户端扫描二维码并确认登录。"
                            "二维码仅用于本次登录，请勿转发。"
                        ),
                        Image.fromBytes(challenge.image_bytes),
                    ]
                )
            )

            poll_result = await self._poller.poll(
                event, provider, challenge, attempt.cancel_event
            )
            if poll_result is None:
                return None
            if poll_result.state == LoginPollState.SUCCESS:
                user = await self._get_current_user(
                    provider,
                    poll_result.cookie_header,
                )
                if attempt.cancel_event.is_set() or not await self._sessions.is_current(
                    platform_name, attempt
                ):
                    return None
                self._save_cookie(provider.cookie_config_key, poll_result.cookie_header)
                if user is None:
                    return (
                        f"{platform_name}登录成功，Cookies 已保存。"
                        "当前用户信息获取失败。"
                    )
                return (
                    f"{platform_name}登录成功，Cookies 已保存。"
                    f"当前用户：{self._format_user(user)}。"
                )
            return self._expired_message(provider)
        except PlatformLoginError as exc:
            return self._format_login_error(platform_name, exc)
        except Exception:
            return self._format_login_error(
                platform_name,
                "登录流程异常，请稍后重试。",
            )
        finally:
            await provider.close()
            await self._sessions.remove(platform_name, attempt)

    async def cancel(self, event: AstrMessageEvent) -> str:
        """取消当前管理员私聊发起的登录流程。"""
        session_id = self._session_id(event)
        if not await self._sessions.cancel_session(session_id):
            return "当前私聊没有进行中的平台登录。"
        return "已取消当前私聊中的平台登录。"

    async def logout(self, platform_name: str) -> str:
        """清除指定平台 Cookie，并终止该平台正在进行的登录。"""
        platform_name = platform_name.strip()
        cookie_key = self._cookie_keys.get(platform_name)
        if cookie_key is None:
            return self._unsupported_platform_message(platform_name)

        await self._sessions.cancel_platform(platform_name)
        if not parse_cookie_header(self.cookie_store.get(cookie_key)):
            return f"{platform_name}当前没有已保存的 Cookies。"
        try:
            self._save_cookie(cookie_key, "")
        except PlatformLoginError as exc:
            return str(exc)
        return f"{platform_name}已退出登录，Cookies 已清除。"

    async def status(self) -> str:
        """并发查询所有平台的本地配置状态与当前账号。"""
        return await self._status_service.render(
            self.supported_platforms,
            await self._sessions.active_platforms(),
        )

    async def close(self) -> None:
        """取消并释放插件卸载时仍在进行的登录流程。"""
        attempts = await self._sessions.drain()
        for attempt in attempts:
            attempt.cancel_event.set()
            await attempt.provider.close()

    def _save_cookie(self, cookie_key: str, cookie_header: str) -> None:
        self.cookie_store.save(cookie_key, cookie_header)

    @staticmethod
    async def _get_current_user(
        provider: PlatformLoginProvider,
        cookie_header: str,
    ) -> PlatformUser | None:
        try:
            user = await provider.get_current_user(cookie_header)
        except Exception:
            return None
        if not isinstance(user, PlatformUser):
            return None
        if not user.user_id.strip() and not user.display_name.strip():
            return None
        return user

    @classmethod
    def _format_user(cls, user: PlatformUser) -> str:
        return LoginMessageFormatter.user(user)

    @staticmethod
    def _clean_user_field(value: object) -> str:
        return LoginMessageFormatter.user_field(value)

    def _unsupported_platform_message(self, platform_name: str) -> str:
        return LoginMessageFormatter.unsupported(
            platform_name, self.supported_platforms
        )

    @staticmethod
    def _format_login_error(
        platform_name: str,
        error: PlatformLoginError | str,
    ) -> str:
        """在私聊边界统一平台登录错误格式，并去除重复的平台前缀。"""
        return LoginMessageFormatter.error(platform_name, error)

    @staticmethod
    def _expired_message(provider: PlatformLoginProvider) -> str:
        return LoginMessageFormatter.expired(provider)

    @staticmethod
    def _session_id(event: AstrMessageEvent) -> str:
        return str(event.unified_msg_origin)
