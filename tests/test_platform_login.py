import httpx
import pytest
from astrbot_multi_parser.core.platform_login import (
    HTTPPlatformLoginProvider,
    PlatformLoginError,
    read_login_response_body,
    render_login_qr_png,
)


class StubProvider(HTTPPlatformLoginProvider):
    async def create_qr_challenge(self):
        raise NotImplementedError

    async def poll_qr_status(self, session_key):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_http_login_provider_does_not_close_injected_client():
    client = httpx.AsyncClient()
    provider = StubProvider({}, client=client)

    await provider.close()

    assert client.is_closed is False
    await client.aclose()


@pytest.mark.asyncio
async def test_http_login_provider_closes_owned_client():
    provider = StubProvider({})

    await provider.close()

    assert provider._client.is_closed is True


@pytest.mark.asyncio
async def test_read_login_response_body_rejects_oversized_stream():
    response = httpx.Response(
        200,
        content=b"12345",
        request=httpx.Request("GET", "https://example.com"),
    )

    with pytest.raises(
        PlatformLoginError,
        match="测试平台登录服务响应超过安全限制",
    ):
        await read_login_response_body(response, limit=4, platform="测试平台")


@pytest.mark.asyncio
async def test_read_login_response_body_accepts_exact_limit():
    response = httpx.Response(
        200,
        content=b"1234",
        request=httpx.Request("GET", "https://example.com"),
    )

    assert (
        await read_login_response_body(response, limit=4, platform="测试平台")
        == b"1234"
    )


def test_render_login_qr_png_returns_png_bytes():
    content = render_login_qr_png("https://example.com/login")

    assert content.startswith(b"\x89PNG\r\n\x1a\n")
