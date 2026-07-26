import json

import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.weibo.login import WeiboLoginProvider


def jsonp_response(request: httpx.Request, payload: dict) -> httpx.Response:
    callback = request.url.params["callback"]
    body = f"window.{callback} && {callback}({json.dumps(payload)});"
    return httpx.Response(
        200,
        request=request,
        text=body,
        headers={"Content-Type": "application/javascript"},
    )


def test_weibo_confirmation_token_supports_bounded_nested_payloads():
    token = "确认令牌 value"

    assert (
        WeiboLoginProvider._find_sso_token(
            {"data": {"result": {"login": {"alt": token}}}}
        )
        == token
    )


def test_weibo_confirmation_token_rejects_control_characters():
    assert (
        WeiboLoginProvider._find_sso_token(
            {"data": {"alt": "token\nforbidden"}}
        )
        == ""
    )


@pytest.mark.asyncio
async def test_weibo_qr_login_completes_sso_and_keeps_only_minimal_cookie():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/sso/qrcode/image":
            return jsonp_response(
                request,
                {
                    "retcode": 20000000,
                    "data": {
                        "qrid": "one-time-qrid",
                        "image": "//v2.qr.weibo.cn/inf/gen?token=qr-secret",
                    },
                },
            )
        if request.url.host == "v2.qr.weibo.cn":
            assert "cookie" not in request.headers
            return httpx.Response(
                200,
                request=request,
                content=b"\x89PNG\r\n\x1a\nqr-image",
                headers={"Content-Type": "image/png"},
            )
        if request.url.path == "/sso/qrcode/check":
            assert request.url.params["qrid"] == "one-time-qrid"
            return jsonp_response(
                request,
                {
                    "retcode": 20000000,
                    "data": {"alt": "ALT%2Fone.time-secret=="},
                },
            )
        if request.url.path == "/sso/login.php":
            assert request.url.params["entry"] == "qrcodesso"
            assert request.url.params["alt"] == "ALT%2Fone.time-secret=="
            return jsonp_response(
                request,
                {
                    "retcode": "0",
                    "crossDomainUrlList": [
                        "https://passport.weibo.com/sso/crossdomain"
                        "?ticket=ticket-secret"
                    ],
                },
            )
        assert request.url.host == "passport.weibo.com"
        assert request.url.params["action"] == "login"
        return httpx.Response(
            200,
            request=request,
            headers=[
                (
                    "Set-Cookie",
                    "SUB=session-secret; Domain=.weibo.com; Path=/; Secure",
                ),
                (
                    "Set-Cookie",
                    "SUBP=profile-secret; Domain=.weibo.com; Path=/; Secure",
                ),
                (
                    "Set-Cookie",
                    "WBPSESS=page-secret; Domain=.weibo.com; Path=/; Secure",
                ),
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("SUB", "foreign-secret", domain="example.com")
        provider = WeiboLoginProvider({}, client=client)

        challenge = await provider.create_qr_challenge()
        result = await provider.poll_qr_status(challenge.session_key)

    assert challenge.session_key == "one-time-qrid"
    assert challenge.image_bytes.startswith(b"\x89PNG")
    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == "SUB=session-secret"
    assert all(request.url.scheme == "https" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retcode", "expected_state"),
    [
        (50114001, LoginPollState.WAITING),
        (50114002, LoginPollState.SCANNED),
        (50114003, LoginPollState.EXPIRED),
        (50114015, LoginPollState.EXPIRED),
    ],
)
async def test_weibo_qr_login_maps_poll_states(retcode, expected_state):
    def handler(request: httpx.Request) -> httpx.Response:
        return jsonp_response(request, {"retcode": retcode})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-qrid")

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_weibo_qr_login_rejects_unknown_state_without_leaking_session():
    def handler(request: httpx.Request) -> httpx.Response:
        return jsonp_response(
            request,
            {"retcode": 59999999, "msg": "internal-sensitive-detail"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无法识别") as exc_info:
            await provider.poll_qr_status("one-time-qrid")

    message = str(exc_info.value)
    assert "one-time-qrid" not in message
    assert "internal-sensitive-detail" not in message


@pytest.mark.asyncio
async def test_weibo_qr_login_rejects_untrusted_qr_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return jsonp_response(
            request,
            {
                "retcode": 20000000,
                "data": {
                    "qrid": "secret-qrid",
                    "image": "https://evil.example/steal-login",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    assert "secret-qrid" not in str(exc_info.value)
    assert "evil.example" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_weibo_qr_login_rejects_untrusted_success_url_without_alt_leak():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sso/qrcode/check":
            return jsonp_response(
                request,
                {
                    "retcode": 20000000,
                    "data": {"alt": "ALT-sensitive-confirmation=="},
                },
            )
        return jsonp_response(
            request,
            {
                "retcode": "0",
                "crossDomainUrlList": [
                    "https://evil.example/login?ticket=sensitive-ticket"
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的登录确认") as exc_info:
            await provider.poll_qr_status("one-time-qrid")

    message = str(exc_info.value)
    assert "ALT-sensitive" not in message
    assert "sensitive-ticket" not in message
    assert "evil.example" not in message


@pytest.mark.asyncio
async def test_weibo_qr_login_ignores_untrusted_extra_success_urls():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/sso/qrcode/check":
            return jsonp_response(
                request,
                {"retcode": 20000000, "data": {"alt": "ALT-secret=="}},
            )
        if request.url.path == "/sso/login.php":
            return jsonp_response(
                request,
                {
                    "retcode": "0",
                    "crossDomainUrlList": [
                        "https://evil.example/collect?ticket=secret",
                        "https://passport.weibo.com/sso/crossdomain?ticket=ok",
                    ],
                },
            )
        assert request.url.host == "passport.weibo.com"
        return httpx.Response(
            200,
            request=request,
            headers={
                "Set-Cookie": "SUB=session-secret; Domain=.weibo.com; Path=/; Secure"
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        result = await provider.poll_qr_status("one-time-qrid")

    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == "SUB=session-secret"
    assert all(request.url.host != "evil.example" for request in requests)


@pytest.mark.asyncio
async def test_weibo_qr_login_rejects_untrusted_success_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sso/qrcode/check":
            return jsonp_response(
                request,
                {"retcode": 20000000, "data": {"alt": "ALT-secret=="}},
            )
        if request.url.path == "/sso/login.php":
            return jsonp_response(
                request,
                {
                    "retcode": "0",
                    "crossDomainUrlList": [
                        "https://passport.weibo.com/wbsso/login?ticket=secret"
                    ],
                },
            )
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://evil.example/collect"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的登录确认"):
            await provider.poll_qr_status("one-time-qrid")


@pytest.mark.asyncio
async def test_weibo_qr_login_rejects_oversized_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (WeiboLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_weibo_qr_login_rejects_oversized_qr_image():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sso/qrcode/image":
            return jsonp_response(
                request,
                {
                    "retcode": 20000000,
                    "data": {
                        "qrid": "one-time-qrid",
                        "image": "https://v2.qr.weibo.cn/inf/gen",
                    },
                },
            )
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (WeiboLoginProvider.MAX_QR_IMAGE_BYTES + 1),
            headers={"Content-Type": "image/png"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_weibo_qr_login_reports_network_failure_without_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("failed-sensitive-url", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    message = str(exc_info.value)
    assert "failed-sensitive-url" not in message
    assert "sso/qrcode" not in message


@pytest.mark.asyncio
async def test_weibo_qr_login_stops_on_risk_control_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            text="<html><title>安全验证</title><div>geetest</div></html>",
            headers={"Content-Type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeiboLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="人机或设备验证"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_weibo_cookie_header_enforces_name_and_domain_whitelists():
    async with httpx.AsyncClient() as client:
        client.cookies.set("SUB", "weibo-secret", domain=".weibo.com")
        client.cookies.set("SUBP", "not-needed", domain=".weibo.com")
        client.cookies.set("SUB", "foreign-secret", domain="example.com")
        provider = WeiboLoginProvider({}, client=client)

        assert provider._cookie_header() == "SUB=weibo-secret"
