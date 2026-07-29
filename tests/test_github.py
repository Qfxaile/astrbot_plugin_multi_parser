import httpx
import pytest
from astrbot_multi_parser.core.contracts import ParseContext
from astrbot_multi_parser.platforms.github import GitHubParser


async def test_github_match_supports_repository_root_links():
    parser = GitHubParser({})

    assert await parser.match(
        ParseContext(text="https://github.com/Soulter/astrbot_plugin_github_cards")
    )
    assert await parser.match(
        ParseContext(
            text="仓库：https://www.github.com/AstrBotDevs/AstrBot/?tab=readme"
        )
    )
    assert await parser.match(
        ParseContext(text="https://github.com/AstrBotDevs/AstrBot.git")
    )


async def test_github_match_rejects_users_and_repository_subpaths():
    parser = GitHubParser({})

    assert not await parser.match(ParseContext(text="https://github.com/AstrBotDevs"))
    for subpath in (
        "issues/1",
        "pull/2",
        "blob/main/README.md",
        "tree/main",
        "commit/abcdef",
    ):
        assert not await parser.match(
            ParseContext(text=f"https://github.com/AstrBotDevs/AstrBot/{subpath}")
        )


async def test_github_parse_uses_official_opengraph_card_from_repository_page(
    monkeypatch,
):
    parser = GitHubParser({})
    materialized = {}
    fetched_pages = []

    async def fetch_opengraph_url(client, repository_url):
        fetched_pages.append(repository_url)
        return "https://opengraph.githubassets.com/official-hash/AstrBotDevs/AstrBot"

    async def materialize_images(result, client, referer):
        materialized["referer"] = referer
        return result

    monkeypatch.setattr(
        parser,
        "_fetch_opengraph_url",
        fetch_opengraph_url,
        raising=False,
    )
    monkeypatch.setattr(parser, "materialize_images", materialize_images)

    result = await parser.parse(
        ParseContext(text="https://github.com/AstrBotDevs/AstrBot.git#readme")
    )

    assert result.platform == "github"
    assert result.image_urls == [
        "https://opengraph.githubassets.com/official-hash/AstrBotDevs/AstrBot"
    ]
    assert fetched_pages == ["https://github.com/AstrBotDevs/AstrBot"]
    assert materialized["referer"] == "https://github.com/AstrBotDevs/AstrBot"


async def test_github_fetches_opengraph_card_after_safe_redirect():
    requested_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path == "/astrbotdevs/astrbot":
            return httpx.Response(
                301,
                headers={"Location": "https://github.com/AstrBotDevs/AstrBot"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            text=(
                '<html><head><meta content="https://repository-images.'
                'githubusercontent.com/123/card.png" property="og:image">'
                "</head></html>"
            ),
            request=request,
        )

    parser = GitHubParser({})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        card_url = await parser._fetch_opengraph_url(
            client,
            "https://github.com/astrbotdevs/astrbot",
        )

    assert card_url == ("https://repository-images.githubusercontent.com/123/card.png")
    assert requested_urls == [
        "https://github.com/astrbotdevs/astrbot",
        "https://github.com/AstrBotDevs/AstrBot",
    ]


async def test_github_rejects_untrusted_opengraph_card_url():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            text=(
                '<meta property="og:image" content="https://attacker.example/card.png">'
            ),
            request=request,
        )

    parser = GitHubParser({})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.InvalidURL, match="untrusted OpenGraph image URL"):
            await parser._fetch_opengraph_url(
                client,
                "https://github.com/AstrBotDevs/AstrBot",
            )


async def test_github_rejects_redirect_to_non_repository_github_host():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            301,
            headers={"Location": "https://api.github.com/repos/AstrBotDevs/AstrBot"},
            request=request,
        )

    parser = GitHubParser({})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.InvalidURL, match="untrusted repository page URL"):
            await parser._fetch_opengraph_url(
                client,
                "https://github.com/AstrBotDevs/AstrBot",
            )


async def test_github_rejects_oversized_repository_page():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(2 * 1024 * 1024 + 1),
            },
            request=request,
        )

    parser = GitHubParser({})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPError, match="repository page too large"):
            await parser._fetch_opengraph_url(
                client,
                "https://github.com/AstrBotDevs/AstrBot",
            )


async def test_github_parse_returns_readable_error_when_card_download_fails(
    monkeypatch,
):
    parser = GitHubParser({})

    async def fetch_opengraph_url(client, repository_url):
        raise httpx.ConnectError("private network detail")

    monkeypatch.setattr(
        parser,
        "_fetch_opengraph_url",
        fetch_opengraph_url,
        raising=False,
    )

    result = await parser.parse(
        ParseContext(text="https://github.com/AstrBotDevs/AstrBot")
    )

    assert result.error == "GitHub仓库卡片请求失败，请稍后重试。"
    assert "private network detail" not in result.error
