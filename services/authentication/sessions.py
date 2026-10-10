"""活动平台登录会话的并发与取消管理。"""

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass
class LoginAttempt:
    session_id: str
    provider: Any
    cancel_event: asyncio.Event


class LoginSessionRegistry:
    """保证同一平台单实例登录，并按私聊范围取消会话。"""

    def __init__(self):
        self._attempts: dict[str, LoginAttempt] = {}
        self._lock = asyncio.Lock()

    async def register(self, platform: str, attempt: LoginAttempt) -> bool:
        async with self._lock:
            if platform in self._attempts:
                return False
            self._attempts[platform] = attempt
            return True

    async def is_current(self, platform: str, attempt: LoginAttempt) -> bool:
        async with self._lock:
            return self._attempts.get(platform) is attempt

    async def remove(self, platform: str, attempt: LoginAttempt) -> None:
        async with self._lock:
            if self._attempts.get(platform) is attempt:
                self._attempts.pop(platform, None)

    async def cancel_session(self, session_id: str) -> int:
        async with self._lock:
            attempts = [
                item
                for item in self._attempts.values()
                if item.session_id == session_id
            ]
            for attempt in attempts:
                attempt.cancel_event.set()
            return len(attempts)

    async def cancel_platform(self, platform: str) -> LoginAttempt | None:
        async with self._lock:
            attempt = self._attempts.get(platform)
            if attempt is not None:
                attempt.cancel_event.set()
            return attempt

    async def active_platforms(self) -> frozenset[str]:
        async with self._lock:
            return frozenset(self._attempts)

    async def drain(self) -> list[LoginAttempt]:
        async with self._lock:
            attempts = list(self._attempts.values())
            self._attempts.clear()
            return attempts
