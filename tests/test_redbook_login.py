import json

import httpx
import pytest
from astrbot_multi_parser.core.authentication import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.redbook.login import RedBookLoginProvider
from astrbot_multi_parser.platforms.redbook.signing import XhshowRequestSigner


class FakeSigner:
    def __init__(self):
        self.calls = []

    def sign(self, method, uri, a1_value, payload):
        self.calls.append((method, uri, a1_value, dict(payload)))
        return {"X-S": "signed-value", "X-T": "1234567890"}


def set_a1_cookie(client: httpx.AsyncClient) -> None:
    client.cookies.set("a1", "official-anonymous-cookie", domain=".xiaohongshu.com")


def qr_create_response(**overrides) -> dict:
    data = {
        "url": "https://www.xiaohongshu.com/mobile/login/qr",
        "qr_id": "qr-id",
        "code": "one-time-code",
        "expire": 60,
        **overrides,
    }
    return {"code": 0, "data": data}


def test_redbook_xhshow_signer_uses_synchronized_timestamp(monkeypatch):
    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.redbook.signing.time.time",
        lambda: 1_700_000_000.123,
    )

    headers = XhshowRequestSigner().sign(
        "POST",
        RedBookLoginProvider.QR_CREATE_URI,
        "official-anonymous-cookie",
        {"qr_type": 1},
    )

    assert headers["X-S"].startswith("XYW_")
    assert headers["X-T"] == "1700000000123"


@pytest.mark.asyncio
async def test_redbook_qr_create_uses_signed_official_request_and_local_session():
    request_seen = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = request
        return httpx.Response(200, json=qr_create_response(), request=request)

    signer = FakeSigner()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=signer)

        challenge = await provider.create_qr_challenge()

    assert challenge.image_bytes.startswith(b"\x89PNG")
    assert challenge.expires_in_seconds == 60
    assert "qr-id" not in challenge.session_key
    assert "one-time-code" not in challenge.session_key
    assert request_seen is not None
    assert request_seen.method == "POST"
    assert str(request_seen.url) == RedBookLoginProvider.QR_CREATE_URL
    assert json.loads(request_seen.content) == {"qr_type": 1}
    assert request_seen.headers["X-S"] == "signed-value"
    assert request_seen.headers["X-T"] == "1234567890"
    assert request_seen.headers["Content-Type"] == "application/json;charset=UTF-8"
    assert signer.calls == [
        (
            "POST",
            RedBookLoginProvider.QR_CREATE_URI,
            "official-anonymous-cookie",
            {"qr_type": 1},
        )
    ]


@pytest.mark.asyncio
async def test_redbook_qr_create_bootstraps_official_a1_cookie():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "www.xiaohongshu.com":
            return httpx.Response(
                200,
                headers={
                    "Set-Cookie": (
                        "a1=official-bootstrap-cookie; "
                        "Domain=.xiaohongshu.com; Path=/; Secure"
                    )
                },
                text="<html>official page</html>",
                request=request,
            )
        return httpx.Response(200, json=qr_create_response(), request=request)

    signer = FakeSigner()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RedBookLoginProvider({}, client=client, signer=signer)

        await provider.create_qr_challenge()

    assert requested_urls == [
        RedBookLoginProvider.BOOTSTRAP_URL,
        RedBookLoginProvider.QR_CREATE_URL,
    ]
    assert signer.calls[0][2] == "official-bootstrap-cookie"


@pytest.mark.asyncio
async def test_redbook_qr_create_reuses_configured_official_a1_cookie():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(200, json=qr_create_response(), request=request)

    signer = FakeSigner()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RedBookLoginProvider(
            {"redbook_cookies": "a1=official-config-cookie"},
            client=client,
            signer=signer,
        )
        await provider.create_qr_challenge()

    assert requested_hosts == ["edith.xiaohongshu.com"]
    assert signer.calls[0][2] == "official-config-cookie"


@pytest.mark.asyncio
async def test_redbook_qr_create_does_not_reuse_configured_account_session():
    request_cookie = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_cookie
        request_cookie = request.headers.get("Cookie", "")
        return httpx.Response(200, json=qr_create_response(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RedBookLoginProvider(
            {
                "redbook_cookies": "a1=official-config-cookie; "
                "web_session=previous-account-secret"
            },
            client=client,
            signer=FakeSigner(),
        )

        await provider.create_qr_challenge()

    assert "a1=official-config-cookie" in request_cookie
    assert "web_session" not in request_cookie
    assert "previous-account-secret" not in request_cookie


@pytest.mark.asyncio
async def test_redbook_qr_create_explains_missing_official_a1_cookie():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>official page</html>", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(
            PlatformLoginError,
            match="真实浏览器环境设置的 a1",
        ):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        (0, LoginPollState.WAITING),
        ("1", LoginPollState.SCANNED),
        (3, LoginPollState.EXPIRED),
    ],
)
async def test_redbook_qr_poll_maps_known_states(status, expected_state):
    responses = [
        httpx.Response(200, json=qr_create_response()),
        httpx.Response(200, json={"code": 0, "data": {"codeStatus": status}}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

        result = await provider.poll_qr_status(challenge.session_key)

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_redbook_qr_success_saves_only_whitelisted_platform_cookies():
    responses = [
        httpx.Response(200, json=qr_create_response()),
        httpx.Response(
            200,
            headers=[
                (
                    "Set-Cookie",
                    "web_session=session-secret; Domain=.xiaohongshu.com; Path=/",
                ),
                (
                    "Set-Cookie",
                    "gid=must-not-save; Domain=.xiaohongshu.com; Path=/",
                ),
            ],
            json={"code": 0, "data": {"code_status": 2}},
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        client.cookies.set(
            "web_session",
            "foreign-secret",
            domain="example.com",
        )
        client.cookies.set("a1", "foreign-a1", domain="example.com")
        client.cookies.set(
            "web_session",
            "invalid;session",
            domain="invalid.xiaohongshu.com",
        )
        client.cookies.set(
            "a1",
            "invalid;a1",
            domain="invalid.xiaohongshu.com",
        )
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

        result = await provider.poll_qr_status(challenge.session_key)

    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == (
        "a1=official-anonymous-cookie; web_session=session-secret"
    )
    assert "gid" not in result.cookie_header
    assert "foreign-secret" not in result.cookie_header
    assert "foreign-a1" not in result.cookie_header
    assert "invalid" not in result.cookie_header


@pytest.mark.asyncio
async def test_redbook_qr_success_requires_web_session_in_addition_to_a1():
    responses = [
        httpx.Response(200, json=qr_create_response()),
        httpx.Response(200, json={"code": 0, "data": {"code_status": 2}}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

        with pytest.raises(PlatformLoginError, match="缺少有效登录凭据"):
            await provider.poll_qr_status(challenge.session_key)


@pytest.mark.asyncio
async def test_redbook_qr_success_rejects_untrusted_confirmation_url():
    responses = [
        httpx.Response(200, json=qr_create_response()),
        httpx.Response(
            200,
            headers={
                "Set-Cookie": (
                    "web_session=session-secret; Domain=.xiaohongshu.com; Path=/"
                )
            },
            json={
                "code": 0,
                "data": {
                    "code_status": 2,
                    "redirect_url": "https://example.com/sensitive-confirmation",
                },
            },
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

        with pytest.raises(PlatformLoginError, match="无效的登录确认") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    assert "sensitive-confirmation" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "login_url",
    [
        "http://www.xiaohongshu.com/mobile/login/secret",
        "https://example.com/mobile/login/secret",
        "https://user:password@www.xiaohongshu.com/mobile/login/secret",
        "https://www.xiaohongshu.com:444/mobile/login/secret",
    ],
)
async def test_redbook_qr_create_rejects_untrusted_login_url(login_url):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=qr_create_response(
                url=login_url,
                qr_id="sensitive-qr-id",
                code="sensitive-code",
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())

        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    message = str(exc_info.value)
    assert "sensitive-qr-id" not in message
    assert "sensitive-code" not in message
    assert "secret" not in message


@pytest.mark.asyncio
async def test_redbook_qr_create_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * (RedBookLoginProvider.MAX_RESPONSE_BYTES + 1),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())

        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_redbook_qr_network_failure_does_not_leak_request_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "sensitive-network-detail",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())

        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    assert "sensitive-network-detail" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "body"),
    [
        (
            "text/html",
            '<html><div id="captcha"></div></html>',
        ),
        (
            "application/json",
            {"code": "-13002", "data": {"verify_ticket": "secret-ticket"}},
        ),
    ],
)
async def test_redbook_qr_stops_on_verification_response(content_type, body):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(body, dict):
            return httpx.Response(200, json=body, request=request)
        return httpx.Response(
            200,
            headers={"Content-Type": content_type},
            text=body,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())

        with pytest.raises(
            PlatformLoginError, match="人机、设备验证或风控"
        ) as exc_info:
            await provider.create_qr_challenge()

    assert "secret-ticket" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_redbook_qr_stops_on_http_risk_interception():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(461, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())

        with pytest.raises(PlatformLoginError, match="人机、设备验证或风控"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_redbook_qr_unknown_status_does_not_leak_session_tokens():
    responses = [
        httpx.Response(
            200,
            json=qr_create_response(
                qr_id="sensitive-qr-id",
                code="sensitive-one-time-code",
            ),
        ),
        httpx.Response(200, json={"code": 0, "data": {"code_status": 99}}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        response = responses.pop(0)
        response.request = request
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

        with pytest.raises(PlatformLoginError, match="无法识别") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    message = str(exc_info.value)
    assert "sensitive-qr-id" not in message
    assert "sensitive-one-time-code" not in message


@pytest.mark.asyncio
async def test_redbook_qr_close_clears_temporary_sessions():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=qr_create_response(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        set_a1_cookie(client)
        provider = RedBookLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

        await provider.close()

        with pytest.raises(PlatformLoginError, match="会话无效"):
            await provider.poll_qr_status(challenge.session_key)
