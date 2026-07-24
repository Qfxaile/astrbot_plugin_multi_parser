import json

import httpx
import pytest
from astrbot_multi_parser.core.authentication import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.tieba.login import TiebaLoginProvider


def jsonp_response(request: httpx.Request, payload: dict, **kwargs) -> httpx.Response:
    callback = request.url.params["callback"]
    return httpx.Response(
        200,
        request=request,
        text=f"{callback}({json.dumps(payload, ensure_ascii=False)});",
        **kwargs,
    )


def qr_handler(poll_payload: dict, *, confirm_response=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/api/getqrcode":
            assert request.url.params["tpl"] == "tb"
            assert len(request.url.params["gid"]) == 35
            return jsonp_response(
                request,
                {
                    "errno": 0,
                    "sign": "one-time-sign",
                    "imgurl": "/v2/api/qrcode?sign=one-time-sign",
                },
                headers=[
                    (
                        "Set-Cookie",
                        "BAIDUID=device-secret; Domain=.baidu.com; Path=/",
                    ),
                    (
                        "Set-Cookie",
                        "BIDUPSID=browser-secret; Domain=.baidu.com; Path=/",
                    ),
                    (
                        "Set-Cookie",
                        "PSTM=login-time; Domain=.baidu.com; Path=/",
                    ),
                ],
            )
        if request.url.path == "/v2/api/qrcode":
            return httpx.Response(
                200,
                request=request,
                headers={"Content-Type": "image/png"},
                content=b"\x89PNG\r\n\x1a\nqr-image",
            )
        if request.url.path == "/channel/unicast":
            assert request.url.params["channel_id"] == "one-time-sign"
            return jsonp_response(request, poll_payload)
        if request.url.path == "/v3/login/main/qrbdusslogin":
            assert request.url.params["bduss"] == "one-time-login-token"
            if confirm_response is not None:
                return confirm_response(request)
            return httpx.Response(
                200,
                request=request,
                headers=[
                    (
                        "Set-Cookie",
                        "BDUSS=session-secret; Domain=.baidu.com; Path=/; HttpOnly",
                    ),
                    (
                        "Set-Cookie",
                        "STOKEN=csrf-secret; Domain=passport.baidu.com; Path=/",
                    ),
                    (
                        "Set-Cookie",
                        "UNEXPECTED=ignored; Domain=.baidu.com; Path=/",
                    ),
                ],
            )
        return httpx.Response(404, request=request)

    return handler


@pytest.mark.asyncio
async def test_tieba_qr_login_creates_official_qr_challenge():
    handler = qr_handler({"errno": 1})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert challenge.session_key == "one-time-sign"
    assert challenge.image_bytes.startswith(b"\x89PNG")
    assert challenge.expires_in_seconds == provider.QR_EXPIRES_IN_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("poll_payload", "expected_state"),
    [
        ({"errno": 1}, LoginPollState.WAITING),
        (
            {"errno": 0, "channel_v": json.dumps({"status": 1})},
            LoginPollState.SCANNED,
        ),
        ({"errno": 2}, LoginPollState.EXPIRED),
        (
            {"errno": 0, "channel_v": json.dumps({"status": 2})},
            LoginPollState.EXPIRED,
        ),
    ],
)
async def test_tieba_qr_login_maps_poll_states(poll_payload, expected_state):
    handler = qr_handler(poll_payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        result = await provider.poll_qr_status(challenge.session_key)

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_tieba_qr_login_saves_only_expected_domain_cookies():
    handler = qr_handler(
        {
            "errno": 0,
            "channel_v": json.dumps(
                {"status": 0, "v": "one-time-login-token"}
            ),
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("BDUSS", "foreign-secret", domain="example.com")
        provider = TiebaLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        result = await provider.poll_qr_status(challenge.session_key)

    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == (
        "BAIDUID=device-secret; BIDUPSID=browser-secret; PSTM=login-time; "
        "BDUSS=session-secret"
    )
    assert "foreign-secret" not in result.cookie_header
    assert "UNEXPECTED" not in result.cookie_header
    assert "STOKEN" not in result.cookie_header


@pytest.mark.asyncio
async def test_tieba_qr_login_rejects_unknown_state_without_leaking_tokens():
    handler = qr_handler(
        {
            "errno": 0,
            "channel_v": json.dumps({"status": 99, "v": "private-token"}),
        }
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="无法识别") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    message = str(exc_info.value)
    assert "private-token" not in message
    assert challenge.session_key not in message


@pytest.mark.asyncio
async def test_tieba_qr_login_rejects_untrusted_qr_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return jsonp_response(
            request,
            {
                "errno": 0,
                "sign": "one-time-sign",
                "imgurl": "https://example.com/steal-login",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    assert "one-time-sign" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tieba_qr_login_accepts_official_host_without_scheme():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/v2/api/getqrcode":
            return jsonp_response(
                request,
                {
                    "errno": 0,
                    "sign": "one-time-sign",
                    "imgurl": (
                        "passport.baidu.com/v2/api/qrcode"
                        "?sign=one-time-sign"
                    ),
                },
            )
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "image/png"},
            content=b"\x89PNG\r\n\x1a\nqr-image",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert challenge.image_bytes.startswith(b"\x89PNG")
    assert requested_urls[1].startswith(
        "https://passport.baidu.com/v2/api/qrcode?"
    )
    assert "/v2/api/passport.baidu.com/" not in requested_urls[1]


@pytest.mark.asyncio
async def test_tieba_qr_login_rejects_untrusted_success_redirect():
    def redirect_response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://example.com/login"},
        )

    handler = qr_handler(
        {
            "errno": 0,
            "channel_v": json.dumps(
                {"status": 0, "v": "one-time-login-token"}
            ),
        },
        confirm_response=redirect_response,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="无效的登录确认"):
            await provider.poll_qr_status(challenge.session_key)


@pytest.mark.asyncio
async def test_tieba_qr_login_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (TiebaLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_tieba_qr_login_returns_safe_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network failed with private-token", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    assert "private-token" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_tieba_qr_login_stops_on_verification_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            text="<html><title>百度安全验证</title></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = TiebaLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="人机或设备验证"):
            await provider.create_qr_challenge()
