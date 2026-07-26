import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.bilibili.login import BilibiliLoginProvider


@pytest.mark.asyncio
async def test_bilibili_qr_login_collects_only_expected_domain_cookies():
    responses = [
        httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "url": "https://passport.bilibili.com/h5-app/passport/login/scan",
                    "qrcode_key": "one-time-key",
                },
            },
        ),
        httpx.Response(
            200,
            headers=[
                (
                    "Set-Cookie",
                    "SESSDATA=session-secret; Domain=.bilibili.com; Path=/",
                ),
                (
                    "Set-Cookie",
                    "bili_jct=csrf-secret; Domain=.bilibili.com; Path=/",
                ),
            ],
            json={"code": 0, "data": {"code": 0}},
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("foreign", "must-not-save", domain="example.com")
        provider = BilibiliLoginProvider({}, client=client)

        challenge = await provider.create_qr_challenge()
        result = await provider.poll_qr_status(challenge.session_key)

    assert challenge.session_key == "one-time-key"
    assert challenge.image_bytes.startswith(b"\x89PNG")
    assert result.state == LoginPollState.SUCCESS
    assert "SESSDATA=session-secret" in result.cookie_header
    assert "bili_jct=csrf-secret" in result.cookie_header
    assert "foreign" not in result.cookie_header


@pytest.mark.asyncio
async def test_bilibili_cookie_header_rejects_unsafe_values():
    async with httpx.AsyncClient() as client:
        client.cookies.set("SESSDATA", "unsafe;value", domain=".bilibili.com")
        client.cookies.set("bili_jct", "safe-value", domain=".bilibili.com")
        provider = BilibiliLoginProvider({}, client=client)

        assert provider._cookie_header() == "bili_jct=safe-value"


@pytest.mark.asyncio
async def test_bilibili_qr_login_rejects_untrusted_qr_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "code": 0,
                "data": {
                    "url": "https://example.com/steal-login",
                    "qrcode_key": "secret-key",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BilibiliLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    assert "secret-key" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_bilibili_qr_login_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (BilibiliLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BilibiliLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "expected_state"),
    [
        (86101, LoginPollState.WAITING),
        (86090, LoginPollState.SCANNED),
        (86038, LoginPollState.EXPIRED),
    ],
)
async def test_bilibili_qr_login_maps_poll_states(code, expected_state):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"code": 0, "data": {"code": code}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = BilibiliLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-key")

    assert result.state == expected_state
