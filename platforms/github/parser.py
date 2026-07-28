"""解析 GitHub 仓库主页并发送官方 OpenGraph 卡片。"""

import re
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from ...core.contracts import ParseContext, ParseResult
from ...core.parser import BaseParser


class GitHubParser(BaseParser):
    """识别公开仓库主页，并下载 GitHub 官方仓库卡片。"""

    name = "github"
    display_name = "GitHub"
    image_host_suffixes = ("opengraph.githubassets.com",)
    URL_PATTERN = re.compile(
        r"https?://(?:www\.)?github\.com/[^\s<>\[\](){}，。！？、；：'\"`]+",
        re.IGNORECASE,
    )
    OWNER_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
    REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,100}")
    OPENGRAPH_URL = "https://opengraph.githubassets.com/{cache_key}/{owner}/{repo}"
    HEADERS = {
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    async def match(self, context: ParseContext) -> bool:
        return self._find_repository(context.combined_text) is not None

    async def parse(self, context: ParseContext) -> ParseResult:
        repository = self._find_repository(context.combined_text)
        if repository is None:
            return ParseResult(platform=self.name, error="未找到 GitHub 仓库链接。")

        owner, repo = repository
        repository_url = f"https://github.com/{owner}/{repo}"
        card_url = self.OPENGRAPH_URL.format(
            cache_key=uuid4().hex,
            owner=owner,
            repo=repo,
        )
        result = ParseResult(
            platform=self.name,
            image_urls=[card_url],
            image_download_headers={
                "Referer": repository_url,
                "User-Agent": self.HEADERS["User-Agent"],
            },
        )
        try:
            async with httpx.AsyncClient(
                timeout=self.request_timeout,
                follow_redirects=False,
                headers=self.HEADERS,
            ) as client:
                return await self.materialize_images(result, client, repository_url)
        except httpx.HTTPError:
            return ParseResult(
                platform=self.name,
                error="GitHub仓库卡片请求失败，请稍后重试。",
            )

    @classmethod
    def _find_repository(cls, text: str) -> tuple[str, str] | None:
        """返回消息中的首个仓库根链接，忽略所有仓库子路径。"""
        for match in cls.URL_PATTERN.finditer(text):
            candidate = match.group(0).rstrip(".,!?;:")
            if repository := cls._parse_repository_url(candidate):
                return repository
        return None

    @classmethod
    def _parse_repository_url(cls, url: str) -> tuple[str, str] | None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 80, 443}
        ):
            return None

        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) != 2:
            return None
        owner, repo = path_parts
        if repo.lower().endswith(".git"):
            repo = repo[:-4]
        if (
            cls.OWNER_PATTERN.fullmatch(owner) is None
            or cls.REPOSITORY_PATTERN.fullmatch(repo) is None
            or repo in {".", ".."}
        ):
            return None
        return owner, repo
