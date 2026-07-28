import httpx
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


async def test_github_parse_builds_official_opengraph_card(monkeypatch):
    parser = GitHubParser({})
    materialized = {}

    async def materialize_images(result, client, referer):
        materialized["referer"] = referer
        return result

    monkeypatch.setattr(parser, "materialize_images", materialize_images)
    monkeypatch.setattr(
        "astrbot_multi_parser.platforms.github.parser.uuid4",
        lambda: type("FixedUUID", (), {"hex": "0123456789abcdef"})(),
    )

    result = await parser.parse(
        ParseContext(text="https://github.com/AstrBotDevs/AstrBot.git#readme")
    )

    assert result.platform == "github"
    assert result.image_urls == [
        "https://opengraph.githubassets.com/0123456789abcdef/AstrBotDevs/AstrBot"
    ]
    assert materialized["referer"] == "https://github.com/AstrBotDevs/AstrBot"


async def test_github_parse_returns_readable_error_when_card_download_fails(
    monkeypatch,
):
    parser = GitHubParser({})

    async def materialize_images(result, client, referer):
        raise httpx.ConnectError("private network detail")

    monkeypatch.setattr(parser, "materialize_images", materialize_images)

    result = await parser.parse(
        ParseContext(text="https://github.com/AstrBotDevs/AstrBot")
    )

    assert result.error == "GitHub仓库卡片请求失败，请稍后重试。"
    assert "private network detail" not in result.error
