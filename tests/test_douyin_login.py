import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.douyin.login import DouyinLoginProvider

CONFIGURED_TTWID = {"douyin_cookies": "ttwid=configured-device"}


@pytest.mark.asyncio
async def test_douyin_qr_login_creates_challenge_from_trusted_image_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "sso.douyin.com":
            assert request.url.params["need_logo"] == "true"
            assert request.url.params["device_platform"] == "web_app"
            assert request.headers["Origin"] == "https://www.douyin.com"
            assert "application/json" in request.headers["Accept"]
            assert "ttwid=configured-device" in request.headers["Cookie"]
            assert "sessionid" not in request.headers["Cookie"]
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {
                        "qrcode": "https://p3-passport.byteimg.com/login.jpg",
                        "token": "one-time-token",
                    }
                },
            )
        assert "Cookie" not in request.headers
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xff\xd9",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(
            {"douyin_cookies": ("ttwid=configured-device; sessionid=must-not-reuse")},
            client=client,
        )
        challenge = await provider.create_qr_challenge()

    assert challenge.session_key == "one-time-token"
    assert challenge.image_bytes == b"\xff\xd8\xff\xd9"
    assert challenge.expires_in_seconds == 180


@pytest.mark.asyncio
async def test_douyin_qr_login_collects_only_expected_domain_cookies():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/check_qrconnect/":
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {
                        "status": "3",
                        "redirect_url": (
                            "https://sso.douyin.com/login/complete?ticket=hidden"
                        ),
                    }
                },
            )
        if request.url.host == "sso.douyin.com":
            return httpx.Response(
                302,
                request=request,
                headers=[
                    ("Location", "https://www.douyin.com/"),
                    (
                        "Set-Cookie",
                        "sessionid=session-secret; Domain=.douyin.com; Path=/",
                    ),
                    (
                        "Set-Cookie",
                        "ttwid=device-session; Domain=.douyin.com; Path=/",
                    ),
                    (
                        "Set-Cookie",
                        "not_needed=ignore-me; Domain=.douyin.com; Path=/",
                    ),
                ],
            )
        return httpx.Response(200, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("foreign", "must-not-save", domain="example.com")
        provider = DouyinLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-token")

    assert result.state == LoginPollState.SUCCESS
    assert "sessionid=session-secret" in result.cookie_header
    assert "ttwid=device-session" in result.cookie_header
    assert "not_needed" not in result.cookie_header
    assert "foreign" not in result.cookie_header


@pytest.mark.asyncio
async def test_douyin_qr_login_rejects_untrusted_qr_url_without_leaking_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "data": {
                    "qrcode": "https://example.com/steal-login.jpg",
                    "token": "secret-one-time-token",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    assert "secret-one-time-token" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_douyin_qr_login_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (DouyinLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_douyin_qr_login_reports_platform_verification_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html>captcha verify security</html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="人机或设备验证"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_douyin_qr_login_reports_json_verification_without_leaking_ticket():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"data": {"captcha": {"verify_ticket": "ticket-secret"}}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="人机或设备验证") as exc_info:
            await provider.create_qr_challenge()

    assert "ticket-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_douyin_qr_login_does_not_follow_qr_image_redirect():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if request.url.host == "sso.douyin.com":
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {
                        "qrcode": "https://p3-passport.byteimg.com/login.jpg",
                        "token": "one-time-token",
                    }
                },
            )
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://example.com/private.jpg"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="不安全的重定向"):
            await provider.create_qr_challenge()

    assert requested_hosts == ["sso.douyin.com", "p3-passport.byteimg.com"]


@pytest.mark.asyncio
async def test_douyin_qr_login_hides_network_error_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "failed for https://example.com/?token=network-secret",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    assert "network-secret" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_douyin_qr_login_bootstraps_ttwid_through_trusted_callback():
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append((request.url.host, request.url.path))
        if request.url.host == "ttwid.bytedance.com":
            assert request.method == "POST"
            assert request.headers["Origin"] == "https://www.douyin.com"
            return httpx.Response(
                200,
                request=request,
                json={"redirect_url": "https://www.douyin.com/ttwid/callback"},
            )
        if request.url.path == "/ttwid/callback":
            return httpx.Response(
                302,
                request=request,
                headers=[
                    ("Location", "/ttwid/final"),
                    (
                        "Set-Cookie",
                        "ttwid=bootstrapped-device; Domain=.douyin.com; Path=/",
                    ),
                ],
            )
        if request.url.path == "/ttwid/final":
            return httpx.Response(
                200,
                request=request,
                headers={"Content-Type": "text/html; charset=utf-8"},
                text="<html><title>Douyin</title></html>",
            )
        if request.url.host == "sso.douyin.com":
            assert "ttwid=bootstrapped-device" in request.headers["Cookie"]
            return httpx.Response(
                200,
                request=request,
                json={
                    "data": {
                        "qrcode": "https://p3-passport.byteimg.com/login.jpg",
                        "token": "one-time-token",
                    }
                },
            )
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "image/jpeg"},
            content=b"\xff\xd8\xff\xd9",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert challenge.session_key == "one-time-token"
    assert requested_paths[:4] == [
        ("ttwid.bytedance.com", "/ttwid/union/register/"),
        ("www.douyin.com", "/ttwid/callback"),
        ("www.douyin.com", "/ttwid/final"),
        ("sso.douyin.com", "/get_qrcode/"),
    ]


@pytest.mark.asyncio
async def test_douyin_qr_login_rejects_untrusted_ttwid_callback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"redirect_url": "https://example.com/collect"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="不安全的回调地址") as exc_info:
            await provider.create_qr_challenge()

    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_douyin_qr_login_rejects_untrusted_ttwid_callback_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ttwid.bytedance.com":
            return httpx.Response(
                200,
                request=request,
                json={"redirect_url": "https://www.douyin.com/ttwid/callback"},
            )
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://example.com/collect"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="不安全的重定向") as exc_info:
            await provider.create_qr_challenge()

    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_douyin_qr_login_rejects_oversized_ttwid_callback_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ttwid.bytedance.com":
            return httpx.Response(
                200,
                request=request,
                json={"redirect_url": "https://www.douyin.com/ttwid/callback"},
            )
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (DouyinLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_douyin_qr_login_reports_generic_html_as_invalid_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text="<html><title>Temporary service page</title></html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider(CONFIGURED_TTWID, client=client)
        with pytest.raises(PlatformLoginError, match="无效响应") as exc_info:
            await provider.create_qr_challenge()

    assert "人机或设备验证" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        ("1", LoginPollState.WAITING),
        ("new", LoginPollState.WAITING),
        ("2", LoginPollState.SCANNED),
        ("scanned", LoginPollState.SCANNED),
        ("4", LoginPollState.EXPIRED),
        ("5", LoginPollState.EXPIRED),
        ("expired", LoginPollState.EXPIRED),
    ],
)
async def test_douyin_qr_login_maps_poll_states(status, expected_state):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"data": {"status": status}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-token")

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_douyin_qr_login_rejects_unknown_status_without_leaking_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"data": {"status": "unexpected"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = DouyinLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无法识别") as exc_info:
            await provider.poll_qr_status("secret-one-time-token")

    assert "secret-one-time-token" not in str(exc_info.value)
