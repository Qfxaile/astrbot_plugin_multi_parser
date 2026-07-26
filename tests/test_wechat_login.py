import json

import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    LoginPollState,
    PlatformLoginError,
)
from astrbot_multi_parser.platforms.wechat.login import WeChatLoginProvider

QR_UUID = "021cBO5n3Vya000m"
QR_IMAGE_URL = f"https://open.weixin.qq.com/connect/qrcode/{QR_UUID}"
QR_IMAGE = b"\x89PNG\r\n\x1a\nmock-png"


def qr_page_response(request: httpx.Request) -> httpx.Response | None:
    if request.url.host != "open.weixin.qq.com":
        return None
    if request.url.path == "/connect/qrconnect":
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=(
                '<html><img class="js_qrcode_img web_qrcode_img" '
                f'src="/connect/qrcode/{QR_UUID}"></html>'
            ),
        )
    if request.url.path == f"/connect/qrcode/{QR_UUID}":
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "image/png"},
            content=QR_IMAGE,
        )
    return None


def poll_response(request: httpx.Request, status: int, code: str = ""):
    return httpx.Response(
        200,
        request=request,
        headers={"Content-Type": "application/javascript"},
        content=(
            f"window.wx_errcode={status};window.wx_code='{code}';"
        ).encode(),
    )


@pytest.mark.asyncio
async def test_wechat_qr_login_creates_trusted_oauth_challenge_without_cookie_leak():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        assert "Cookie" not in request.headers
        response = qr_page_response(request)
        assert response is not None
        if request.url.path == "/connect/qrconnect":
            assert request.url.params["appid"] == WeChatLoginProvider.OAUTH_APP_ID
            assert request.url.params["scope"] == "snsapi_login"
            assert request.url.params["state"] == "wechat_login"
            assert request.url.params["redirect_uri"].startswith(
                "https://yuanbao.tencent.com/scan?nonce="
            )
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        client.cookies.set("hy_token", "yuanbao-secret", domain="yuanbao.tencent.com")
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()

    assert requested_hosts == ["open.weixin.qq.com", "open.weixin.qq.com"]
    assert challenge.image_bytes == QR_IMAGE
    assert challenge.expires_in_seconds == 300
    assert QR_UUID not in challenge.session_key


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_state"),
    [
        (408, LoginPollState.WAITING),
        (404, LoginPollState.SCANNED),
        (402, LoginPollState.EXPIRED),
    ],
)
async def test_wechat_qr_login_maps_poll_states(status, expected_state):
    def handler(request: httpx.Request) -> httpx.Response:
        response = qr_page_response(request)
        if response is not None:
            return response
        assert request.url.host == "long.open.weixin.qq.com"
        assert request.url.params["uuid"] == QR_UUID
        return poll_response(request, status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        result = await provider.poll_qr_status(challenge.session_key)

    assert result.state == expected_state


@pytest.mark.asyncio
async def test_wechat_qr_login_passes_scanned_status_to_next_long_poll():
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        response = qr_page_response(request)
        if response is not None:
            return response
        poll_count += 1
        if poll_count == 1:
            assert "last" not in request.url.params
            return poll_response(request, 404)
        assert request.url.params["last"] == "404"
        return poll_response(request, 408)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        first = await provider.poll_qr_status(challenge.session_key)
        second = await provider.poll_qr_status(challenge.session_key)

    assert first.state == LoginPollState.SCANNED
    assert second.state == LoginPollState.WAITING


@pytest.mark.asyncio
async def test_wechat_qr_login_exchanges_code_for_minimum_yuanbao_credentials():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        response = qr_page_response(request)
        if response is not None:
            return response
        if request.url.host == "long.open.weixin.qq.com":
            assert "Cookie" not in request.headers
            return poll_response(request, 405, "wechat-auth-code")
        assert request.url.host == "yuanbao.tencent.com"
        assert request.url.path == "/api/joint/login"
        assert request.method == "POST"
        assert not request.url.query
        assert request.headers["Origin"] == "https://yuanbao.tencent.com"
        assert request.headers["X-Source"] == "web"
        assert json.loads(request.content) == {
            "type": "wx",
            "jsCode": "wechat-auth-code",
            "appid": "wx12b75947931a04ec",
            "apiFeature": "team",
        }
        return httpx.Response(
            200,
            request=request,
            json={
                "code": 0,
                "data": {
                    "userId": "user-secret",
                    "token": "token-secret",
                    "ignored": "must-not-save",
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        result = await provider.poll_qr_status(challenge.session_key)

    assert requested_hosts == [
        "open.weixin.qq.com",
        "open.weixin.qq.com",
        "long.open.weixin.qq.com",
        "yuanbao.tencent.com",
    ]
    assert result.state == LoginPollState.SUCCESS
    assert result.cookie_header == "yb_user_id=user-secret; yb_token=token-secret"
    assert "ignored" not in result.cookie_header


@pytest.mark.asyncio
async def test_wechat_qr_login_rejects_untrusted_qr_url_without_requesting_it():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html"},
            text=(
                '<img class="js_qrcode_img web_qrcode_img" '
                'src="https://example.com/connect/qrcode/secret-uuid">'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="无效的二维码") as exc_info:
            await provider.create_qr_challenge()

    assert requested_hosts == ["open.weixin.qq.com"]
    assert "secret-uuid" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_wechat_qr_login_rejects_login_api_redirect():
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        response = qr_page_response(request)
        if response is not None:
            return response
        if request.url.host == "long.open.weixin.qq.com":
            return poll_response(request, 405, "secret-auth-code")
        return httpx.Response(
            302,
            request=request,
            headers={"Location": "https://example.com/steal-login"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="不安全的重定向") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    assert "example.com" not in requested_hosts
    assert "secret-auth-code" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_wechat_qr_login_rejects_incomplete_yuanbao_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        response = qr_page_response(request)
        if response is not None:
            return response
        if request.url.host == "long.open.weixin.qq.com":
            return poll_response(request, 405, "secret-auth-code")
        return httpx.Response(
            200,
            request=request,
            json={"code": 0, "data": {"userId": "user-secret"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="缺少有效") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    assert "secret-auth-code" not in str(exc_info.value)
    assert "user-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_wechat_qr_login_rejects_oversized_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            content=b"x" * (WeChatLoginProvider.MAX_AUTH_PAGE_BYTES + 1),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="超过安全限制"):
            await provider.create_qr_challenge()


@pytest.mark.asyncio
async def test_wechat_qr_login_hides_network_error_details():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "failed for https://example.com/?nonce=network-secret",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        with pytest.raises(PlatformLoginError, match="请求失败") as exc_info:
            await provider.create_qr_challenge()

    assert "network-secret" not in str(exc_info.value)
    assert "example.com" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_wechat_qr_login_stops_on_yuanbao_verification_page():
    def handler(request: httpx.Request) -> httpx.Response:
        response = qr_page_response(request)
        if response is not None:
            return response
        if request.url.host == "long.open.weixin.qq.com":
            return poll_response(request, 405, "secret-auth-code")
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/html"},
            text="<html>请完成设备验证后重试</html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="人机、设备验证或风控") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    assert "secret-auth-code" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_wechat_qr_login_reports_mobile_cancellation():
    def handler(request: httpx.Request) -> httpx.Response:
        response = qr_page_response(request)
        if response is not None:
            return response
        return poll_response(request, 403)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="手机端取消"):
            await provider.poll_qr_status(challenge.session_key)


@pytest.mark.asyncio
async def test_wechat_qr_login_rejects_unknown_status_without_leaking_session():
    def handler(request: httpx.Request) -> httpx.Response:
        response = qr_page_response(request)
        if response is not None:
            return response
        return poll_response(request, 500, "unknown-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = WeChatLoginProvider({}, client=client)
        challenge = await provider.create_qr_challenge()
        with pytest.raises(PlatformLoginError, match="无法识别") as exc_info:
            await provider.poll_qr_status(challenge.session_key)

    assert challenge.session_key not in str(exc_info.value)
    assert "unknown-secret" not in str(exc_info.value)
