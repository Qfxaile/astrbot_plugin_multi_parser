import httpx
import pytest
from astrbot_multi_parser.core.webpage import (
    TrustedWebPageError,
    fetch_trusted_html,
)


def make_client(handler):
    """构造禁止 httpx 自动跟随重定向的测试客户端。"""
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )


class ChunkedAsyncStream(httpx.AsyncByteStream):
    """按给定分块输出响应正文的 httpx 测试流。"""

    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_fetch_trusted_html_follows_relative_safe_redirect():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/share":
            return httpx.Response(302, headers={"Location": "/item/42"})
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            content="<html>商品</html>".encode(),
        )

    async with make_client(handler) as client:
        page = await fetch_trusted_html(
            client,
            "https://shop.example.com/share",
            ("example.com",),
        )

    assert page.final_url == "https://shop.example.com/item/42"
    assert page.html == "<html>商品</html>"
    assert requested_urls == [
        "https://shop.example.com/share",
        "https://shop.example.com/item/42",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "http://shop.example.com/item",
        "https://shop.example.com.evil.test/item",
        "https://user@shop.example.com/item",
        "https://shop.example.com:444/item",
    ],
)
async def test_fetch_trusted_html_rejects_untrusted_redirect(location):
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(302, headers={"Location": location})

    async with make_client(handler) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品分享链接跳转到不可信域名。$",
        ) as error:
            await fetch_trusted_html(
                client,
                "https://shop.example.com/share",
                ("example.com",),
            )

    assert location not in str(error.value)
    assert requested_urls == ["https://shop.example.com/share"]


@pytest.mark.asyncio
async def test_fetch_trusted_html_rejects_untrusted_start_without_request():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200)

    async with make_client(handler) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品分享链接跳转到不可信域名。$",
        ):
            await fetch_trusted_html(
                client,
                "http://shop.example.com/share",
                ("example.com",),
            )

    assert request_count == 0


@pytest.mark.asyncio
async def test_fetch_trusted_html_rejects_six_redirects():
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            302,
            headers={"Location": f"/redirect/{request_count}"},
        )

    async with make_client(handler) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品页面重定向次数超过安全限制。$",
        ):
            await fetch_trusted_html(
                client,
                "https://shop.example.com/share",
                ("example.com",),
            )

    assert request_count == 6


@pytest.mark.asyncio
async def test_fetch_trusted_html_rejects_redirect_without_location():
    async with make_client(lambda request: httpx.Response(302)) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品分享链接缺少跳转地址。$",
        ):
            await fetch_trusted_html(
                client,
                "https://shop.example.com/share",
                ("example.com",),
            )


@pytest.mark.asyncio
async def test_fetch_trusted_html_rejects_non_html_response():
    async with make_client(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=b"{}",
        )
    ) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品页面不是可读取的 HTML。$",
        ):
            await fetch_trusted_html(
                client,
                "https://shop.example.com/item",
                ("example.com",),
            )


@pytest.mark.asyncio
async def test_fetch_trusted_html_rejects_oversized_content_length():
    async with make_client(
        lambda request: httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(2 * 1024 * 1024 + 1),
            },
        )
    ) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品页面响应过大，已停止解析。$",
        ):
            await fetch_trusted_html(
                client,
                "https://shop.example.com/item",
                ("example.com",),
            )


@pytest.mark.asyncio
async def test_fetch_trusted_html_stops_when_stream_exceeds_limit():
    max_bytes = 10

    async with make_client(
        lambda request: httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            stream=ChunkedAsyncStream([b"123456", b"78901"]),
        )
    ) as client:
        with pytest.raises(
            TrustedWebPageError,
            match="^商品页面响应过大，已停止解析。$",
        ):
            await fetch_trusted_html(
                client,
                "https://shop.example.com/item",
                ("example.com",),
                max_bytes=max_bytes,
            )


@pytest.mark.asyncio
async def test_fetch_trusted_html_preserves_http_errors():
    async with make_client(
        lambda request: httpx.Response(
            404,
            headers={"Content-Type": "text/html"},
            content=b"missing",
        )
    ) as client:
        with pytest.raises(httpx.HTTPStatusError, match="404"):
            await fetch_trusted_html(
                client,
                "https://shop.example.com/missing",
                ("example.com",),
            )
