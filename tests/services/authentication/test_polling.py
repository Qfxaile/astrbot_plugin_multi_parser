import asyncio

import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollResult,
    LoginPollState,
    QRLoginChallenge,
)
from astrbot_multi_parser.services.authentication.polling import QRLoginPoller


class FakeProvider:
    def __init__(self, results):
        self.results = iter(results)
        self.poll_count = 0

    async def poll_qr_status(self, session_key):
        self.poll_count += 1
        return next(self.results)


class FakeEvent:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


def challenge(expires=30):
    return QRLoginChallenge("session", b"qr", expires)


@pytest.mark.asyncio
async def test_poller_notifies_scanned_once_and_returns_success():
    event = FakeEvent()
    provider = FakeProvider(
        [
            LoginPollResult(LoginPollState.SCANNED),
            LoginPollResult(LoginPollState.SCANNED),
            LoginPollResult(LoginPollState.SUCCESS, "session=secret"),
        ]
    )

    result = await QRLoginPoller(0).poll(event, provider, challenge(), asyncio.Event())

    assert result == LoginPollResult(LoginPollState.SUCCESS, "session=secret")
    assert provider.poll_count == 3
    assert len(event.sent) == 1
    assert event.sent[0].chain[0].text == "二维码已扫描，请在手机上确认登录。"


@pytest.mark.asyncio
async def test_poller_returns_expired_when_challenge_has_no_time_remaining():
    provider = FakeProvider([])

    result = await QRLoginPoller().poll(
        FakeEvent(), provider, challenge(expires=0), asyncio.Event()
    )

    assert result == LoginPollResult(LoginPollState.EXPIRED)
    assert provider.poll_count == 0


@pytest.mark.asyncio
async def test_poller_returns_none_when_cancelled_before_next_poll():
    cancel_event = asyncio.Event()
    cancel_event.set()
    provider = FakeProvider([])

    result = await QRLoginPoller().poll(
        FakeEvent(), provider, challenge(), cancel_event
    )

    assert result is None
    assert provider.poll_count == 0
