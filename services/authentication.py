"""协调管理员私聊中的平台登录、取消和凭据持久化。"""

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain

from ..core.http import (
    cookie_config_value,
    parse_cookie_header,
    set_cookie_config_value,
)
from ..core.platform_login import (
    LoginPollState,
    PlatformLoginError,
    PlatformLoginProvider,
    PlatformUser,
)
from ..platforms.registry import PLATFORM_REGISTRY

ProviderFactory = Callable[[], PlatformLoginProvider]


@dataclass
class _ActiveLogin:
    """记录一个平台当前独占的登录流程及其所属私聊。"""

    session_id: str
    provider: PlatformLoginProvider
    cancel_event: asyncio.Event


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
        self._provider_factories = dict(
            provider_factories
            or {
                registration.login_provider_type.display_name: partial(
                    registration.login_provider_type,
                    self.config,
                )
                for registration in PLATFORM_REGISTRY
            }
        )
        self._cookie_keys = {
            registration.login_provider_type.display_name: (
                registration.login_provider_type.cookie_config_key
            )
            for registration in PLATFORM_REGISTRY
        }
        self._active_logins: dict[str, _ActiveLogin] = {}
        self._lock = asyncio.Lock()

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
        attempt = _ActiveLogin(
            session_id=self._session_id(event),
            provider=provider,
            cancel_event=asyncio.Event(),
        )
        async with self._lock:
            if platform_name in self._active_logins:
                await provider.close()
                return f"{platform_name}已有登录流程正在进行，请先取消或等待结束。"
            self._active_logins[platform_name] = attempt

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

            deadline = time.monotonic() + challenge.expires_in_seconds
            scanned_notified = False
            while time.monotonic() < deadline:
                if attempt.cancel_event.is_set():
                    return None

                poll_result = await provider.poll_qr_status(challenge.session_key)
                if poll_result.state == LoginPollState.SUCCESS:
                    user = await self._get_current_user(
                        provider,
                        poll_result.cookie_header,
                    )
                    async with self._lock:
                        if (
                            attempt.cancel_event.is_set()
                            or self._active_logins.get(platform_name) is not attempt
                        ):
                            return None
                        self._save_cookie(
                            provider.cookie_config_key,
                            poll_result.cookie_header,
                        )
                    if user is None:
                        return (
                            f"{platform_name}登录成功，Cookies 已保存。"
                            "当前用户信息获取失败。"
                        )
                    return (
                        f"{platform_name}登录成功，Cookies 已保存。"
                        f"当前用户：{self._format_user(user)}。"
                    )
                if poll_result.state == LoginPollState.EXPIRED:
                    return self._expired_message(provider)
                if poll_result.state == LoginPollState.SCANNED and not scanned_notified:
                    await event.send(
                        MessageChain([Plain("二维码已扫描，请在手机上确认登录。")])
                    )
                    scanned_notified = True

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(
                        attempt.cancel_event.wait(),
                        timeout=min(self.POLL_INTERVAL_SECONDS, remaining),
                    )
                except TimeoutError:
                    pass
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
            async with self._lock:
                if self._active_logins.get(platform_name) is attempt:
                    self._active_logins.pop(platform_name, None)

    async def cancel(self, event: AstrMessageEvent) -> str:
        """取消当前管理员私聊发起的登录流程。"""
        session_id = self._session_id(event)
        async with self._lock:
            attempts = [
                attempt
                for attempt in self._active_logins.values()
                if attempt.session_id == session_id
            ]
            for attempt in attempts:
                attempt.cancel_event.set()
        if not attempts:
            return "当前私聊没有进行中的平台登录。"
        return "已取消当前私聊中的平台登录。"

    async def logout(self, platform_name: str) -> str:
        """清除指定平台 Cookie，并终止该平台正在进行的登录。"""
        platform_name = platform_name.strip()
        cookie_key = self._cookie_keys.get(platform_name)
        if cookie_key is None:
            return self._unsupported_platform_message(platform_name)

        async with self._lock:
            attempt = self._active_logins.get(platform_name)
            if attempt is not None:
                attempt.cancel_event.set()
            if not parse_cookie_header(cookie_config_value(self.config, cookie_key)):
                return f"{platform_name}当前没有已保存的 Cookies。"
            try:
                self._save_cookie(cookie_key, "")
            except PlatformLoginError as exc:
                return str(exc)
        return f"{platform_name}已退出登录，Cookies 已清除。"

    async def status(self) -> str:
        """并发查询所有平台的本地配置状态与当前账号。"""
        async with self._lock:
            active_platforms = frozenset(self._active_logins)
        states = await asyncio.gather(
            *(
                self._platform_status(platform_name, active_platforms)
                for platform_name in self.supported_platforms
            )
        )
        lines = ["平台登录状态："]
        lines.extend(
            f"- {platform_name}：{state}"
            for platform_name, state in zip(
                self.supported_platforms,
                states,
                strict=True,
            )
        )
        return "\n".join(lines)

    async def _platform_status(
        self,
        platform_name: str,
        active_platforms: frozenset[str],
    ) -> str:
        if platform_name in active_platforms:
            return "登录中"
        cookie_key = self._cookie_keys[platform_name]
        cookie_header = str(cookie_config_value(self.config, cookie_key) or "")
        if not parse_cookie_header(cookie_header):
            return "未配置"

        provider = None
        try:
            provider = self._provider_factories[platform_name]()
            user = await self._get_current_user(provider, cookie_header)
        except Exception:
            user = None
        finally:
            if provider is not None:
                try:
                    await provider.close()
                except Exception:
                    pass
        if user is None:
            return "已配置｜用户信息获取失败"
        return f"已配置｜当前用户：{self._format_user(user)}"

    async def close(self) -> None:
        """取消并释放插件卸载时仍在进行的登录流程。"""
        async with self._lock:
            attempts = list(self._active_logins.values())
            self._active_logins.clear()
        for attempt in attempts:
            attempt.cancel_event.set()
            await attempt.provider.close()

    def _save_cookie(self, cookie_key: str, cookie_header: str) -> None:
        # 配置保存失败时恢复内存值，避免解析器使用尚未真正落盘的登录态。
        previous_value = cookie_config_value(self.config, cookie_key)
        set_cookie_config_value(self.config, cookie_key, cookie_header)
        save_config = getattr(self.config, "save_config", None)
        if not callable(save_config):
            return
        try:
            save_config()
        except Exception as exc:
            set_cookie_config_value(self.config, cookie_key, str(previous_value or ""))
            raise PlatformLoginError("Cookies 保存失败，原配置未被修改。") from exc

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
        display_name = cls._clean_user_field(user.display_name)
        user_id = cls._clean_user_field(user.user_id)
        if display_name and user_id:
            return f"{display_name}（UID：{user_id}）"
        if display_name:
            return display_name
        return f"UID：{user_id}"

    @staticmethod
    def _clean_user_field(value: object) -> str:
        return " ".join(str(value or "").split())[:100]

    def _unsupported_platform_message(self, platform_name: str) -> str:
        supported = "、".join(self.supported_platforms)
        if not platform_name:
            return f"请提供平台中文名。当前支持：{supported}。"
        return f"暂不支持“{platform_name}”登录。当前支持：{supported}。"

    @staticmethod
    def _format_login_error(
        platform_name: str,
        error: PlatformLoginError | str,
    ) -> str:
        """在私聊边界统一平台登录错误格式，并去除重复的平台前缀。"""
        detail = str(error).strip() or "发生未知错误，请稍后重试。"
        for prefix in (f"{platform_name}登录", platform_name):
            if detail.startswith(prefix):
                detail = detail[len(prefix) :].lstrip("：:，, ")
                break
        return f"登录失败｜平台：{platform_name}｜原因：{detail}"

    @staticmethod
    def _expired_message(provider: PlatformLoginProvider) -> str:
        message = "二维码已过期，请重新发起登录。"
        if not provider.sms_fallback_available:
            message += "该平台短信登录需要额外人机验证，当前私聊流程暂不支持。"
        return AuthenticationService._format_login_error(
            provider.display_name,
            message,
        )

    @staticmethod
    def _session_id(event: AstrMessageEvent) -> str:
        return str(event.unified_msg_origin)
