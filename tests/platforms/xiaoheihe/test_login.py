import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.xiaoheihe.login import XiaoheiheLoginProvider


class FakeSigner:
    def sign_path(self, path: str) -> dict[str, str | int]:
        return {"hkey": f"signed:{path}", "_time": 1700000000, "nonce": "NONCE"}


def create_payload(**result):
    return {
        "status": "ok",
        "result": {
            "qr_url": ("https://api.xiaoheihe.cn/account/qr_login/?qr=qr-secret"),
            "expire": 120,
            **result,
        },
    }


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_creates_native_app_challenge():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.xiaoheihe.cn"
        assert request.url.path == "/account/get_qrcode_url/"
        assert request.url.params["app"] == "web"
        assert request.url.params["x_app"] == "heybox_website"
        assert request.url.params["hkey"] == "signed:/account/get_qrcode_url/"
        return httpx.Response(200, request=request, json=create_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        challenge = await provider.create_qr_challenge()

    assert provider.qr_scanner_name == ""
    assert challenge.session_key == "qr-secret"
    assert challenge.expires_in_seconds == 120
    assert challenge.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        ("wait", LoginPollState.WAITING),
        ("ready", LoginPollState.SCANNED),
        ("expired", LoginPollState.EXPIRED),
    ],
)
async def test_xiaoheihe_qr_login_maps_native_poll_states(error, expected_state):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/account/qr_state/"
        assert request.url.params["qr"] == "qr-secret"
        assert request.url.params["hkey"] == "signed:/account/qr_state/"
        return httpx.Response(
            200,
            request=request,
            json={"status": "ok", "result": {"error": error}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        result = await provider.poll_qr_status("qr-secret")

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_extracts_native_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "ok",
                "result": {
                    "error": "ok",
                    "pkey": "session-secret",
                    "x_xhh_tokenid": "Bdevice-secret",
                    "profile": {"heybox_id": 123456},
                    "not_needed": "ignore-me",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("foreign", "must-not-save", domain="example.com")
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        result = await provider.poll_qr_status("qr-secret")

    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == (
        "pkey=session-secret; x_xhh_tokenid=Bdevice-secret; heybox_id=123456"
    )
    assert "foreign" not in result.cookie_header
    assert "not_needed" not in result.cookie_header


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_restores_credentials_after_confirmation():
    requested_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/account/qr_state/":
            return httpx.Response(
                200,
                request=request,
                json={
                    "status": "ok",
                    "result": {
                        "error": "ok",
                        "x_xhh_tokenid": "Bdevice-secret",
                    },
                },
            )
        assert request.url.path == "/account/restore_login"
        assert request.url.params["hkey"] == "signed:/account/restore_login"
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "ok",
                "result": {
                    "pkey": "session-secret",
                    "profile": {"heybox_id": "123456"},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        result = await provider.poll_qr_status("qr-secret")

    assert requested_paths == ["/account/qr_state/", "/account/restore_login"]
    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == (
        "pkey=session-secret; x_xhh_tokenid=Bdevice-secret; heybox_id=123456"
    )


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_reports_secondary_verification_during_restore():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/account/qr_state/":
            return httpx.Response(
                200,
                request=request,
                json={"status": "ok", "result": {"error": "ok"}},
            )
        return httpx.Response(
            200,
            request=request,
            json={"status": "need_google_check", "result": {}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="需要完成二次验证"):
            await provider.poll_qr_status("qr-secret")


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_uses_account_detail_id_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "ok",
                "result": {
                    "error": "ok",
                    "pkey": "session-secret",
                    "account_detail": {"userid": "654321"},
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        result = await provider.poll_qr_status("qr-secret")

    assert result.cookie_header == "pkey=session-secret; heybox_id=654321"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "status": "ok",
            "result": {"error": "ok", "profile": {"heybox_id": 1}},
        },
        {"status": "ok", "result": {"error": "ok", "pkey": "secret"}},
    ],
)
async def test_xiaoheihe_qr_login_requires_complete_credentials(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="缺少有效登录凭据"):
            await provider.poll_qr_status("qr-secret")


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_reports_secondary_verification():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"status": "need_google_check", "result": {}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="需要完成二次验证"):
            await provider.poll_qr_status("qr-secret")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qr_url",
    [
        "http://api.xiaoheihe.cn/account/qr_login/?qr=secret",
        "https://evil.example/account/qr_login/?qr=secret",
        "https://api.xiaoheihe.cn.evil.example/account/qr_login/?qr=secret",
        "https://api.xiaoheihe.cn/account/other/?qr=secret",
        "https://api.xiaoheihe.cn/account/qr_login/?qr=",
        "https://api.xiaoheihe.cn/account/qr_login/?qr=one&qr=two",
    ],
)
async def test_xiaoheihe_qr_login_rejects_untrusted_qr_url(qr_url):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "status": "ok",
                "result": {"qr_url": qr_url, "expire": 120},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="无效的二维码登录信息"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_rejects_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://evil.example/redirect"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="不安全的重定向"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (XiaoheiheLoginProvider.MAX_RESPONSE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="响应超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_hides_network_error_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("qr-secret internal.example", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError) as exc_info:
            await provider.create_qr_challenge()

    assert str(exc_info.value) == "小黑盒登录服务请求失败，请稍后重试。"
    assert "qr-secret" not in str(exc_info.value)
    assert "internal.example" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_reports_verification_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            request=request,
            text="<html>captcha</html>",
            headers={"Content-Type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = XiaoheiheLoginProvider({}, client=client, signer=FakeSigner())
        with pytest.raises(PlatformLoginError, match="人机或设备验证"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_xiaoheihe_qr_login_rejects_invalid_session_without_secret_leak():
    provider = XiaoheiheLoginProvider({}, signer=FakeSigner())
    try:
        with pytest.raises(PlatformLoginError) as exc_info:
            await provider.poll_qr_status("secret/invalid")
    finally:
        await provider.close()

    assert "secret/invalid" not in str(exc_info.value)


def test_xiaoheihe_login_requires_account_and_identity_credentials():
    assert XiaoheiheLoginProvider.REQUIRED_COOKIE_NAMES == ("pkey", "heybox_id")
