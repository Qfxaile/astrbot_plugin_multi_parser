import httpx
import pytest
from astrbot_multi_parser.core.parser import BaseParser


class Parser(BaseParser):
    name = "test"


@pytest.mark.asyncio
async def test_base_parser_http_client_uses_timeout_proxy_and_headers(monkeypatch):
    captured = {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    def factory(**kwargs):
        captured.update(kwargs)
        return Client()

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    parser = Parser(
        {
            "request_timeout_seconds": 12,
            "proxy_url": "http://proxy.example:8080",
            "proxy_switches": {"test": True},
        }
    )

    async with parser.http_client(headers={"X-Test": "1"}):
        pass

    assert captured["timeout"] == 12.0
    assert captured["headers"] == {"X-Test": "1"}
    assert captured["proxy"] == "http://proxy.example:8080"
