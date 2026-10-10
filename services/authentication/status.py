"""查询各平台本地登录凭据和当前账号状态。"""

from collections.abc import Awaitable, Callable, Mapping

from ...core.http import parse_cookie_header
from ...core.platform_login import PlatformLoginProvider, PlatformUser
from .cookie_store import CookieStore
from .messages import LoginMessageFormatter

ProviderFactory = Callable[[], PlatformLoginProvider]
UserLookup = Callable[[PlatformLoginProvider, str], Awaitable[PlatformUser | None]]


class LoginStatusService:
    """隔离登录状态查询的 Cookie 读取、Provider 生命周期和用户文案。"""

    def __init__(
        self,
        cookie_store: CookieStore,
        provider_factories: Mapping[str, ProviderFactory],
        cookie_keys: Mapping[str, str],
        user_lookup: UserLookup,
    ) -> None:
        self.cookie_store = cookie_store
        self.provider_factories = provider_factories
        self.cookie_keys = cookie_keys
        self.user_lookup = user_lookup

    async def render(
        self,
        platforms: tuple[str, ...],
        active_platforms: frozenset[str],
    ) -> str:
        states = await self._collect(platforms, active_platforms)
        lines = ["平台登录状态："]
        lines.extend(
            f"- {platform}：{state}"
            for platform, state in zip(platforms, states, strict=True)
        )
        return "\n".join(lines)

    async def _collect(
        self,
        platforms: tuple[str, ...],
        active_platforms: frozenset[str],
    ) -> list[str]:
        import asyncio

        return list(
            await asyncio.gather(
                *(
                    self._platform_state(platform, active_platforms)
                    for platform in platforms
                )
            )
        )

    async def _platform_state(
        self,
        platform: str,
        active_platforms: frozenset[str],
    ) -> str:
        if platform in active_platforms:
            return "登录中"
        cookie_header = str(self.cookie_store.get(self.cookie_keys[platform]) or "")
        if not parse_cookie_header(cookie_header):
            return "未配置"

        provider = None
        try:
            provider = self.provider_factories[platform]()
            user = await self.user_lookup(provider, cookie_header)
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
        return f"已配置｜当前用户：{LoginMessageFormatter.user(user)}"
