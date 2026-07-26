import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.zhihu.login import ZhihuLoginProvider


def bootstrap_response(
    request: httpx.Request,
    *,
    browser_id: str = "official-browser-id",
) -> httpx.Response | None:
    """为二维码测试模拟知乎登录页明确下发设备标识。"""
    if request.method != "GET" or request.url.path != "/signin":
        return None
    return httpx.Response(
        200,
        request=request,
        headers={"x-du-bid": browser_id},
        text="<html>signin</html>",
    )


@pytest.mark.asyncio
async def test_zhihu_qr_login_creates_challenge_from_trusted_url():
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        assert request.method == "POST"
        assert request.url.path == "/api/v3/account/api/login/qrcode"
        assert request.headers["x-du-bid"] == "official-browser-id"
        return httpx.Response(
            200,
            request=request,
            json={
                "token": "one-time-token",
                "link": "https://www.zhihu.com/account/scan/login",
                "expiresAt": 180,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert challenge.session_key == "one-time-token"
    assert challenge.image_bytes.startswith(b"\x89PNG")
    assert challenge.expires_in_seconds == 180
    assert requested_paths == [
        "/signin",
        "/api/v3/account/api/login/qrcode",
    ]


@pytest.mark.asyncio
async def test_zhihu_qr_login_reuses_trusted_bootstrap_cookie_as_header():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/signin":
            return httpx.Response(
                200,
                request=request,
                headers={
                    "Set-Cookie": (
                        "x-du-bid=official-cookie-id; Domain=.zhihu.com; Path=/; Secure"
                    )
                },
                text="<html>signin</html>",
            )
        assert request.headers["x-du-bid"] == "official-cookie-id"
        return httpx.Response(
            200,
            request=request,
            json={
                "token": "one-time-token",
                "link": "https://www.zhihu.com/account/scan/login",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert challenge.session_key == "one-time-token"


@pytest.mark.asyncio
async def test_zhihu_qr_login_collects_only_expected_domain_cookies():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/one-time-token/scan_info")
        return httpx.Response(
            200,
            request=request,
            headers=[
                (
                    "Set-Cookie",
                    "z_c0=session-secret; Domain=.zhihu.com; Path=/",
                ),
                (
                    "Set-Cookie",
                    "d_c0=device-secret; Domain=.zhihu.com; Path=/",
                ),
                (
                    "Set-Cookie",
                    "_xsrf=not-needed; Domain=.zhihu.com; Path=/",
                ),
            ],
            json={"accessToken": "must-not-save"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("z_c0", "foreign-secret", domain="example.com")
        client.cookies.set("foreign", "must-not-save", domain="example.com")
        provider = ZhihuLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-token")

    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == "z_c0=session-secret; d_c0=device-secret"
    assert "_xsrf" not in result.cookie_header
    assert "foreign" not in result.cookie_header
    assert "must-not-save" not in result.cookie_header


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_state"),
    [
        ({"status": 0}, LoginPollState.WAITING),
        ({"status": 1}, LoginPollState.SCANNED),
        (
            {
                "status": 5,
                "newToken": {
                    "token": "replacement-secret",
                    "link": "https://www.zhihu.com/account/scan/replacement",
                },
            },
            LoginPollState.EXPIRED,
        ),
    ],
)
async def test_zhihu_qr_login_maps_poll_states(payload, expected_state):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-token")

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_unknown_status_without_leaking_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"status": 9, "token": "response-secret"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无法识别") as exc_info:
            await provider.poll_qr_status("request-secret")

    assert "request-secret" not in str(exc_info.value)
    assert "response-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_untrusted_qr_url_without_leaking_token():
    def handler(request: httpx.Request) -> httpx.Response:
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        return httpx.Response(
            200,
            request=request,
            json={
                "token": "secret-one-time-token",
                "link": "https://example.com/steal-login",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    assert "secret-one-time-token" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (ZhihuLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_oversized_bootstrap_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=(b"x" * (ZhihuLoginProvider.MAX_BOOTSTRAP_RESPONSE_BYTES + 1)),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_redirect_without_following_target():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://example.com/private"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="不安全的重定向"):
            await provider.create_qr_challenge()

    assert requested_hosts == ["www.zhihu.com", "www.zhihu.com"]


@pytest.mark.asyncio
async def test_zhihu_qr_login_follows_only_trusted_bootstrap_redirects():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/signin":
            return httpx.Response(
                302,
                request=request,
                headers={"Location": "https://www.zhihu.com/signin/bootstrap"},
            )
        if request.url.path == "/signin/bootstrap":
            return httpx.Response(
                200,
                request=request,
                headers={"x-du-bid": "redirect-browser-id"},
                text="<html>signin</html>",
            )
        assert request.headers["x-du-bid"] == "redirect-browser-id"
        return httpx.Response(
            200,
            request=request,
            json={
                "token": "one-time-token",
                "link": "https://www.zhihu.com/account/scan/login",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert challenge.session_key == "one-time-token"
    assert requested_urls == [
        "https://www.zhihu.com/signin",
        "https://www.zhihu.com/signin/bootstrap",
        "https://www.zhihu.com/api/v3/account/api/login/qrcode",
    ]


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_untrusted_bootstrap_redirect():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://example.com/steal-device"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="不安全的重定向"):
            await provider.create_qr_challenge()

    assert requested_hosts == ["www.zhihu.com"]


@pytest.mark.asyncio
async def test_zhihu_qr_login_reports_platform_verification_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<html><div id="captcha"></div></html>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="人机或设备验证"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_zhihu_qr_login_reports_json_verification_without_leaking_ticket():
    def handler(request: httpx.Request) -> httpx.Response:
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        return httpx.Response(
            200,
            request=request,
            json={
                "error": {
                    "code": 40321,
                    "verify_ticket": "ticket-secret",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="人机或设备验证") as exc_info:
            await provider.create_qr_challenge()

    assert "ticket-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_zhihu_qr_login_hides_network_error_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "failed for https://example.com/?token=network-secret",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    assert "network-secret" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_zhihu_qr_login_reports_required_browser_id():
    def handler(request: httpx.Request) -> httpx.Response:
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        return httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "code": 1000,
                    "message": "required request header x-du-bid is missing",
                    "detail": "request detail must not leak",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="浏览器设备标识") as exc_info:
            await provider.create_qr_challenge()

    assert "request detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_zhihu_qr_login_does_not_misreport_generic_code_1000():
    def handler(request: httpx.Request) -> httpx.Response:
        bootstrap = bootstrap_response(request)
        if bootstrap is not None:
            return bootstrap
        return httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "code": 1000,
                    "message": "ordinary invalid parameter secret-detail",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    assert "浏览器设备标识" not in str(exc_info.value)
    assert "secret-detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_zhihu_qr_login_rejects_unsafe_bootstrap_browser_id():
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(
            200,
            request=request,
            headers={"x-du-bid": "unsafe;browser-id"},
            text="<html>signin</html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="官方浏览器下发"):
            await provider.create_qr_challenge()

    assert requested_paths == ["/signin"]


@pytest.mark.asyncio
async def test_zhihu_qr_login_does_not_derive_browser_id_from_existing_cookies():
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, request=request, text="<html>signin</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("d_c0", "device-cookie", domain=".zhihu.com")
        client.cookies.set("x-du-bid", "preexisting-id", domain=".zhihu.com")
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="官方浏览器下发"):
            await provider.create_qr_challenge()

    assert requested_paths == ["/signin"]


@pytest.mark.asyncio
async def test_zhihu_qr_login_requires_z_c0_without_leaking_access_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"accessToken": "access-token-secret"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("d_c0", "device-secret", domain=".zhihu.com")
        provider = ZhihuLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="缺少有效登录凭据") as exc_info:
            await provider.poll_qr_status("one-time-token")

    assert "access-token-secret" not in str(exc_info.value)
    assert "device-secret" not in str(exc_info.value)
