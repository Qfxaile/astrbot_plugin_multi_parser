"""协调二维码登录轮询与用户可见的扫描状态提示。"""

import asyncio
import time

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain

from ..core.platform_login import (
    LoginPollResult,
    LoginPollState,
    PlatformLoginProvider,
    QRLoginChallenge,
)


class QRLoginPoller:
    """轮询二维码状态，处理扫描提示、超时和会话取消。"""

    def __init__(self, poll_interval_seconds: float = 2.0) -> None:
        self.poll_interval_seconds = poll_interval_seconds

    async def poll(
        self,
        event: AstrMessageEvent,
        provider: PlatformLoginProvider,
        challenge: QRLoginChallenge,
        cancel_event: asyncio.Event,
    ) -> LoginPollResult | None:
        """等待平台返回终态；取消时返回 ``None``。"""
        deadline = time.monotonic() + challenge.expires_in_seconds
        scanned_notified = False
        while time.monotonic() < deadline:
            if cancel_event.is_set():
                return None

            result = await provider.poll_qr_status(challenge.session_key)
            if result.state in {LoginPollState.SUCCESS, LoginPollState.EXPIRED}:
                return result
            if result.state == LoginPollState.SCANNED and not scanned_notified:
                await event.send(
                    MessageChain([Plain("二维码已扫描，请在手机上确认登录。")])
                )
                scanned_notified = True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(
                    cancel_event.wait(),
                    timeout=min(self.poll_interval_seconds, remaining),
                )
            except TimeoutError:
                continue
            return None

        return LoginPollResult(LoginPollState.EXPIRED)
